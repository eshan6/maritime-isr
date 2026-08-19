"""Satellite imaging opportunities over AIS gaps — "was anyone watching?"

**The question this answers.** A vessel switched its AIS transponder off at a
known place and time, and switched it back on at another known place and time.
Radar satellites see ships whether or not those ships are broadcasting. So:
during the silence, did a Sentinel-1 pass photograph a patch of ocean the
vessel *must* have been inside?

That is answerable today, from two tables already on disk, and it needs no
pixels:

  * ``scene_catalog`` — 636 Sentinel-1 records landed 2026-07-29 by
    ``ingest s1 --catalog-only``. Each carries ``footprint_wkt`` (the outline
    of the rectangle the satellite imaged) and ``acquired_at`` (when). These
    are catalogue entries, **not imagery** — see ADR-013's 1 GB cap and
    ADR-017. We hold the index card, not the photograph.
  * ``gfw_ais_gaps`` — GFW's AIS gap events, each carrying ``gap_off_lat/lon``
    (where the broadcast stopped), ``gap_on_lat/lon`` (where it resumed) and
    the two timestamps.

**What a row here may claim, and what it may not.** A ``confirmed`` row says:
*an image exists whose footprint necessarily contained this vessel, and we
have not looked at it.* It says nothing whatever about what the image shows —
no detection, no confirmation the vessel was where GFW thought, nothing about
behaviour. Turning one of these into a dark-vessel finding requires the pixels
and a detector, which is Phase 1 and is parked.

Nor does this re-assess the gap. Whether the gap was intentional stays Global
Fishing Watch's determination (ADR-017's consequence clause); what is ours is
the geometry.

**Why it is worth having anyway.** It is the first analytical claim in this
system that is ours rather than a third party's, and it produces a shopping
list: a named set of scene ids where downloading a few hundred megabytes would
resolve a concrete question about a specific hull. That is a far better
argument for un-parking Phase 1 than "SAR would be nice."

**Not fusion-core work.** This joins two specific sources and produces its own
table; it never touches ``fusion/`` (CLAUDE.md §4.5), following the same
posture ``ingest/sanctions_match.py`` states for itself. It lives at package
root beside ``ports.py`` because it belongs to neither ingest (it fetches
nothing) nor the fusion core.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Optional

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely import wkt as shapely_wkt

from .config import CLI
from .db import connect, table_exists
from .ingest.landing import land_table, read_table, stamp_envelope, stamp_h3

TABLE = "sar_imaging_opportunity"
SOURCE_ID = "sar-overpass-geometry"

#: Mean Earth radius, metres (IUGG). Used for the local planar projection.
EARTH_R_M = 6_371_008.8

#: Top speed a vessel is assumed capable of sustaining, in knots.
#:
#: Deliberately generous for the merchant traffic in this AOI, and generous is
#: the safe direction: a higher speed makes the reachable region *larger*,
#: which makes a `confirmed` containment *harder* to claim. The error therefore
#: runs toward under-claiming, which is the direction ADR-004 asks for.
#:
#: It is a declared assumption, not a measurement, and it is written onto every
#: row as `v_max_knots` so no reader has to go looking for it.
V_MAX_DEFAULT_KN = 20.0

KN_TO_MS = 0.514444

#: A radius of exactly zero makes a degenerate geometry that shapely treats as
#: empty, which would silently drop a scene acquired at the very instant the
#: gap opened. One metre keeps the geometry valid and is far below any
#: resolution that matters here.
MIN_RADIUS_M = 1.0

#: Area ratio at or above which the reachable region counts as fully contained.
#: Not 1.0 exactly: the planar projection and polygon discretisation both leave
#: floating-point dust, and a 0.99997 containment is a containment.
CONTAINED_RATIO = 0.9999

#: Vertices used to approximate each reachable-circle. 180 puts the chord error
#: under 0.02% of the radius — immaterial next to the speed assumption above.
CIRCLE_QUAD_SEGS = 45

TIERS = ("confirmed", "partial", "none", "unknown")


# ---------------------------------------------------------------------------
# geometry — pure, no I/O, all distances in metres in a local planar frame
# ---------------------------------------------------------------------------

def _to_local(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection about (lat0, lon0), metres.

    Planar geometry needs a planar frame. Over the few hundred kilometres a
    Sentinel-1 footprint spans, at Arabian Sea latitudes, equirectangular
    distortion is well under a percent — immaterial against a speed assumption
    we are deliberately rounding up. The frame is rebuilt per gap so the origin
    is always near the geometry it measures.
    """
    x = EARTH_R_M * math.radians(lon - lon0) * math.cos(math.radians(lat0))
    y = EARTH_R_M * math.radians(lat - lat0)
    return x, y


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def _disc(cx: float, cy: float, radius_m: float) -> Polygon:
    return Point(cx, cy).buffer(max(radius_m, MIN_RADIUS_M),
                                quad_segs=CIRCLE_QUAD_SEGS)


def reachable_region(*, off_xy: Optional[tuple[float, float]],
                     on_xy: Optional[tuple[float, float]],
                     secs_since_off: float, secs_until_on: float,
                     v_ms: float) -> tuple[Optional[Polygon], str]:
    """Where the vessel can have been at one instant during the gap.

    Returns ``(region, basis)``. The vessel went dark at ``off`` and resurfaced
    at ``on``; at the moment of a satellite pass it must be:

      * within ``v * secs_since_off`` of ``off`` — a disc growing from where it
        vanished, and
      * within ``v * secs_until_on`` of ``on`` — a disc shrinking toward where
        it reappeared.

    The overlap of those two discs is the answer: lens-shaped, narrow at both
    ends of the gap and widest in the middle. It is the same uncertainty cone
    the glossary describes, evaluated at one moment rather than swept.

    When only one endpoint has a position, the corresponding single disc is
    returned and the basis says so — a weaker but still honest bound. With
    neither, there is no geometry and the caller must record that it could not
    assess rather than that it found nothing (ADR-021).
    """
    if off_xy is not None and on_xy is not None:
        a = _disc(*off_xy, v_ms * secs_since_off)
        b = _disc(*on_xy, v_ms * secs_until_on)
        lens = a.intersection(b)
        if lens.is_empty or lens.area <= 0:
            # Unreachable at this speed. The caller raises v_max above the
            # implied speed before calling, so this is defence in depth rather
            # than an expected branch.
            return None, "unreachable"
        return lens, "lens"
    if off_xy is not None:
        return _disc(*off_xy, v_ms * secs_since_off), "forward_cone"
    if on_xy is not None:
        return _disc(*on_xy, v_ms * secs_until_on), "backward_cone"
    return None, "no_position"


def footprint_polygon(footprint_wkt: str, lat0: float, lon0: float) -> Optional[Polygon]:
    """Scene footprint WKT (EPSG:4326) projected into the local frame.

    Copernicus returns POLYGON for a single footprint and occasionally
    MULTIPOLYGON; both are handled. AOI v1 does not reach the antimeridian, so
    no dateline wrapping is attempted — a footprint that crossed it would come
    back with a longitude span above 180 degrees and is rejected rather than
    silently mis-projected.
    """
    try:
        geom = shapely_wkt.loads(footprint_wkt)
    except Exception:
        return None
    if geom.is_empty:
        return None

    polys = list(getattr(geom, "geoms", [geom]))
    projected = []
    for poly in polys:
        if not isinstance(poly, Polygon):
            continue
        lons = [c[0] for c in poly.exterior.coords]
        if lons and (max(lons) - min(lons)) > 180.0:
            continue  # antimeridian-crossing footprint: out of AOI, skip
        ring = [_to_local(lat, lon, lat0, lon0)
                for lon, lat in poly.exterior.coords]
        if len(ring) >= 4:
            projected.append(Polygon(ring))
    if not projected:
        return None
    merged = unary_union(projected)
    return merged if not merged.is_empty else None


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------

def _ts(value) -> Optional[datetime]:
    """Coerce a landed timestamp to tz-aware UTC. None when unusable."""
    if value is None:
        return None
    dt = value
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def effective_v_ms(*, off: Optional[tuple[float, float]],
                   on: Optional[tuple[float, float]],
                   duration_s: float,
                   v_max_kn: float = V_MAX_DEFAULT_KN) -> tuple[float, Optional[float], bool]:
    """Speed to use for the reachable region, plus the speed the gap implies.

    A gap whose endpoints are further apart than ``v_max`` can explain is not a
    data error to discard — a vessel that appears to have teleported is a
    **spoofing tell** and CLAUDE.md §6 names discarding it as an anti-pattern.
    So rather than dropping the row or returning empty geometry, the assumed
    speed is raised just above what the gap requires, the geometry stays valid,
    and the fact is recorded on the row for someone to look at.

    Returns ``(v_ms, implied_kn, exceeded)``.
    """
    implied_kn = None
    if off is not None and on is not None and duration_s > 0:
        straight_m = math.hypot(on[0] - off[0], on[1] - off[1])
        implied_kn = (straight_m / duration_s) / KN_TO_MS
    if implied_kn is not None and implied_kn > v_max_kn:
        # 5% headroom so the two discs actually overlap rather than touching at
        # a single point, which has zero area and would read as "unreachable".
        return implied_kn * 1.05 * KN_TO_MS, implied_kn, True
    return v_max_kn * KN_TO_MS, implied_kn, False


def assess_pass(gap: dict, scene: dict, *,
                v_max_kn: float = V_MAX_DEFAULT_KN) -> Optional[dict]:
    """One (gap, scene) pair → an opportunity row, or None if the scene was not
    acquired during the gap.

    The returned dict is geometry and description only; provenance is stamped
    by :func:`run`.
    """
    t_off, t_on = _ts(gap.get("start_time")), _ts(gap.get("end_time"))
    t_scene = _ts(scene.get("acquired_at"))
    if t_off is None or t_on is None or t_scene is None:
        return None
    if not (t_off <= t_scene <= t_on) or t_on <= t_off:
        return None

    off_lat, off_lon = _f(gap.get("gap_off_lat")), _f(gap.get("gap_off_lon"))
    on_lat, on_lon = _f(gap.get("gap_on_lat")), _f(gap.get("gap_on_lon"))

    # Local frame origin: the midpoint of whatever positions we have.
    known = [(la, lo) for la, lo in ((off_lat, off_lon), (on_lat, on_lon))
             if la is not None and lo is not None]
    if not known:
        return dict(tier="unknown", geometry_basis="no_position",
                    scene_id=scene.get("scene_id"),
                    reason="gap carries neither an off nor an on position, so "
                           "no reachable region can be computed")
    lat0 = sum(k[0] for k in known) / len(known)
    lon0 = sum(k[1] for k in known) / len(known)

    off_xy = _to_local(off_lat, off_lon, lat0, lon0) \
        if off_lat is not None and off_lon is not None else None
    on_xy = _to_local(on_lat, on_lon, lat0, lon0) \
        if on_lat is not None and on_lon is not None else None

    duration_s = (t_on - t_off).total_seconds()
    v_ms, implied_kn, exceeded = effective_v_ms(
        off=off_xy, on=on_xy, duration_s=duration_s, v_max_kn=v_max_kn)

    region, basis = reachable_region(
        off_xy=off_xy, on_xy=on_xy,
        secs_since_off=(t_scene - t_off).total_seconds(),
        secs_until_on=(t_on - t_scene).total_seconds(),
        v_ms=v_ms)
    if region is None:
        return dict(tier="unknown", geometry_basis=basis,
                    scene_id=scene.get("scene_id"),
                    reason=f"reachable region could not be computed ({basis})")

    fp = footprint_polygon(scene.get("footprint_wkt") or "", lat0, lon0)
    if fp is None:
        return dict(tier="unknown", geometry_basis=basis,
                    scene_id=scene.get("scene_id"),
                    reason="scene footprint is missing or unparseable")

    region_area = region.area
    covered = region.intersection(fp).area
    fraction = (covered / region_area) if region_area > 0 else 0.0
    if fraction >= CONTAINED_RATIO:
        tier = "confirmed"
    elif fraction > 0:
        tier = "partial"
    else:
        tier = "none"

    centroid = region.centroid
    # Invert the projection for the centroid so the row carries a real position
    # (and therefore real H3 cells) rather than frame-local metres.
    c_lat = lat0 + math.degrees(centroid.y / EARTH_R_M)
    c_lon = lon0 + math.degrees(centroid.x / (EARTH_R_M * math.cos(math.radians(lat0))))

    return dict(
        tier=tier,
        geometry_basis=basis,
        scene_id=scene.get("scene_id"),
        scene_acquired_at=t_scene,
        orbit_direction=scene.get("orbit_direction"),
        relative_orbit=scene.get("relative_orbit"),
        scene_mode=scene.get("mode"),
        scene_polarizations=scene.get("polarizations"),
        scene_status=scene.get("status"),
        # Whether the pixels are actually on disk. `cataloged` means we hold
        # the catalogue entry only — the whole point of this table is naming
        # the scenes for which that is worth changing.
        scene_has_pixels=str(scene.get("status") or "").lower()
        in ("raw", "calibrated", "detected"),
        hours_into_gap=(t_scene - t_off).total_seconds() / 3600.0,
        reachable_area_km2=region_area / 1e6,
        covered_area_km2=covered / 1e6,
        coverage_fraction=fraction,
        reachable_centroid_lat=c_lat,
        reachable_centroid_lon=c_lon,
        v_max_knots=v_max_kn,
        implied_speed_kn=implied_kn,
        implied_speed_exceeds_vmax=exceeded,
        reason=None,
    )


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_flagged_gaps(*, flagged_only: bool = True) -> list[dict]:
    """AIS gap rows to assess.

    ``gfw_intentional_disabling`` lands as an INTEGER on the real corpus and a
    BOOLEAN on the scenario one — the same divergence `api/service.py` handles
    — so the truthiness test is written to accept either rather than assuming.
    """
    rows = read_table("gfw_ais_gaps")
    if not flagged_only:
        return rows
    out = []
    for r in rows:
        v = r.get("gfw_intentional_disabling")
        if v is None:
            continue
        try:
            if int(v) == 1:
                out.append(r)
        except (TypeError, ValueError):
            continue
    return out


def load_scenes() -> list[dict]:
    """Sentinel-1 catalogue entries with a usable footprint and time.

    Opened read-only, which DuckDB refuses when the database file does not
    exist yet. A missing catalogue is "nothing landed", not a crash — the
    caller prints the connector to run.
    """
    try:
        con = connect(read_only=True)
    except Exception:
        return []
    try:
        if not table_exists(con, "scene_catalog"):
            return []
        cols = ("scene_id, footprint_wkt, orbit_direction, relative_orbit, "
                "acquired_at, mode, polarizations, status")
        rows = con.execute(
            f"SELECT {cols} FROM scene_catalog "
            "WHERE footprint_wkt IS NOT NULL AND footprint_wkt <> '' "
            "AND acquired_at IS NOT NULL"
        ).fetchall()
        names = [c.strip() for c in cols.split(",")]
        return [dict(zip(names, r)) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def opportunities_for_gap(gap: dict, scenes: Iterable[dict], *,
                          v_max_kn: float = V_MAX_DEFAULT_KN) -> list[dict]:
    """Every assessable (gap, scene) pair for one gap, best tier first.

    A gap with no pass at all still returns one row, tier ``none`` and an empty
    scene id. That is deliberate: without it, a gap that was evaluated and
    found unwatched is indistinguishable from a gap the job never reached —
    exactly the absence-versus-breakage confusion ADR-021 exists to stop.
    """
    rows = [r for r in (assess_pass(gap, s, v_max_kn=v_max_kn) for s in scenes)
            if r is not None and r.get("tier") != "none"]
    if not rows:
        rows = [dict(tier="none", geometry_basis="evaluated", scene_id="",
                     v_max_knots=v_max_kn,
                     reason="no Sentinel-1 pass was acquired over the reachable "
                            "region while this vessel was dark")]
    order = {t: i for i, t in enumerate(TIERS)}
    rows.sort(key=lambda r: (order.get(r["tier"], 9),
                             -(r.get("coverage_fraction") or 0.0)))
    return rows


#: Confidence by tier. A confirmed containment is a geometric fact given the
#: speed assumption, so it is high but not 1.0 — the assumption is real. A
#: partial overlap is a weaker statement about area, not a probability that the
#: vessel was seen, and is scored to reflect that it needs the pixels before it
#: means anything.
CONFIDENCE_BY_TIER = {"confirmed": 0.9, "partial": 0.4, "none": None,
                      "unknown": None}


def _row_for_landing(gap: dict, opp: dict) -> dict:
    row = dict(opp)
    row["gap_event_id"] = gap.get("event_id")
    row["vessel_id"] = gap.get("vessel_id")
    row["gap_start"] = _ts(gap.get("start_time"))
    row["gap_end"] = _ts(gap.get("end_time"))
    row["gap_duration_hours"] = _f(gap.get("gap_duration_hours")) \
        or _f(gap.get("duration_hours"))
    row["gap_off_lat"] = _f(gap.get("gap_off_lat"))
    row["gap_off_lon"] = _f(gap.get("gap_off_lon"))
    row["gap_on_lat"] = _f(gap.get("gap_on_lat"))
    row["gap_on_lon"] = _f(gap.get("gap_on_lon"))
    row["gfw_implied_speed_kn"] = _f(gap.get("gap_implied_speed_kn"))
    row["scene_id"] = row.get("scene_id") or ""
    row.setdefault("scene_acquired_at", None)

    # The sentence an analyst reads. Written here, once, so the API and the
    # incident report cannot drift into two different phrasings of it.
    row["statement"] = _statement(row)

    is_syn = bool(gap.get("is_synthetic"))
    source_id = "synthetic-scenario" if is_syn else SOURCE_ID
    stamp_envelope(
        row,
        source_id=source_id,
        source_ref=f"gap:{row['gap_event_id']}|scene:{row['scene_id'] or 'none'}",
        # When the phenomenon was observed: the satellite pass if there was
        # one, otherwise the gap itself. Never `now` — that is ingested_at.
        acquired_at=row.get("scene_acquired_at") or row.get("gap_start")
        or datetime.now(timezone.utc),
        confidence=CONFIDENCE_BY_TIER.get(row["tier"]),
        is_synthetic=is_syn,
    )
    stamp_h3(row, "reachable_centroid_lat", "reachable_centroid_lon")
    if not row.get("h3_r7"):
        stamp_h3(row, "gap_off_lat", "gap_off_lon")
    return row


def _statement(row: dict) -> str:
    tier = row["tier"]
    if tier == "confirmed":
        return ("A Sentinel-1 pass imaged an area that necessarily contained "
                "this vessel while its AIS was off. The image has not been "
                "examined — no detection is claimed.")
    if tier == "partial":
        pct = (row.get("coverage_fraction") or 0.0) * 100
        return (f"A Sentinel-1 pass covered {pct:.0f}% of the area this vessel "
                "could have occupied while its AIS was off. Whether it was "
                "inside the imaged part is not established.")
    if tier == "none":
        return ("No Sentinel-1 pass was acquired over this vessel's reachable "
                "area while its AIS was off. Nobody was watching.")
    return (f"This gap could not be assessed: {row.get('reason') or 'unknown'}.")


def run(*, v_max_kn: float = V_MAX_DEFAULT_KN,
        flagged_only: bool = True) -> int:
    """Assess every flagged AIS gap against the Sentinel-1 catalogue and land
    the result. Returns the number of rows landed."""
    gaps = load_flagged_gaps(flagged_only=flagged_only)
    if not gaps:
        print("[overpass] no AIS gaps to assess. "
              f"Run `{CLI} ingest gfw-events --kind gaps` first"
              + (", or pass --all-gaps to assess unflagged gaps too."
                 if flagged_only else "."))
        return 0

    scenes = load_scenes()
    if not scenes:
        print("[overpass] no Sentinel-1 scenes in the catalog. "
              f"Run `{CLI} ingest s1 --catalog-only` first.")
        return 0

    print(f"[overpass] {len(gaps):,} gap(s) vs {len(scenes):,} Sentinel-1 "
          f"scene(s), v_max = {v_max_kn:g} kn (assumed, not measured)")

    rows: list[dict] = []
    per_tier = {t: 0 for t in TIERS}
    gaps_with_pass = 0
    speed_flags = 0
    for gap in gaps:
        opps = opportunities_for_gap(gap, scenes, v_max_kn=v_max_kn)
        if any(o["tier"] in ("confirmed", "partial") for o in opps):
            gaps_with_pass += 1
        for o in opps:
            per_tier[o["tier"]] = per_tier.get(o["tier"], 0) + 1
            if o.get("implied_speed_exceeds_vmax"):
                speed_flags += 1
            rows.append(_row_for_landing(gap, o))

    written = land_table(rows, table=TABLE,
                         key_fields=("gap_event_id", "scene_id"),
                         day_field="gap_start")
    landed = sum(written.values())
    print(f"[overpass] landed {landed:,} row(s) into {TABLE} "
          f"({len(written)} day partition(s))")
    print("[overpass]   by tier: " + "   ".join(
        f"{t}: {per_tier.get(t, 0)}" for t in TIERS))
    print(f"[overpass]   {gaps_with_pass} of {len(gaps)} gap(s) had at least "
          "one imaging opportunity")

    confirmed = [r for r in rows if r["tier"] == "confirmed"]
    if confirmed:
        print(f"[overpass]   {len(confirmed)} confirmed opportunit(ies) across "
              f"{len({r['scene_id'] for r in confirmed})} scene(s)")
    else:
        print("[overpass]   no confirmed opportunities — no pass caught a "
              "vessel early enough in its silence to bound it inside one "
              "footprint. Expected on long gaps; see the note below.")

    # The shopping list is drawn from confirmed AND partial rows, because
    # `partial` is the ordinary outcome rather than the exception. At 20 kn the
    # reachable region passes the ~62,500 km2 of a Sentinel-1 IW footprint
    # about four hours into a gap, so anything but a short gap — or a pass near
    # one of its ends — can only ever be partially covered. Printing only
    # confirmed rows would report an empty shopping list on most real corpora
    # while genuinely useful scenes sat in the table.
    candidates = [r for r in rows if r["tier"] in ("confirmed", "partial")]
    need_pixels = sorted(
        {r["scene_id"]: r for r in
         sorted(candidates, key=lambda r: -(r.get("coverage_fraction") or 0))
         if not r.get("scene_has_pixels")}.values(),
        key=lambda r: -(r.get("coverage_fraction") or 0))
    if need_pixels:
        print(f"[overpass]   {len(need_pixels)} scene(s) would need downloading "
              "to resolve these — the shopping list, best coverage first:")
        for r in need_pixels[:10]:
            pct = (r.get("coverage_fraction") or 0) * 100
            print(f"[overpass]     {r['scene_id']}  "
                  f"{r['tier']}, {pct:.0f}% of the searchable area, "
                  f"t+{(r.get('hours_into_gap') or 0):.1f}h into the gap")
        if len(need_pixels) > 10:
            print(f"[overpass]     ... and {len(need_pixels) - 10} more")

    unwatched = sum(1 for r in rows if r["tier"] == "none")
    if unwatched:
        print(f"[overpass]   {unwatched} gap(s) had no pass at all — nobody was "
              "watching, which is itself a finding about coverage.")

    if speed_flags:
        print(f"[overpass]   NOTE: {speed_flags} row(s) describe a gap whose "
              f"endpoints are further apart than {v_max_kn:g} kn can explain. "
              "Per CLAUDE.md §6 that is a spoofing tell, not a bad row — the "
              "assumed speed was raised to keep the geometry valid and the "
              "fact recorded in `implied_speed_exceeds_vmax`.")

    print("[overpass] NOTE: these are imaging OPPORTUNITIES. No image has been "
          "examined and no vessel has been detected.")
    print(f"[overpass] NOTE: at {v_max_kn:g} kn a vessel's reachable area "
          "exceeds one Sentinel-1 footprint about four hours into a gap, so "
          "`partial` is the normal outcome and `confirmed` needs a pass near "
          "the start or end of a silence. A low percentage is geometry, not a "
          "weak result.")
    return landed
