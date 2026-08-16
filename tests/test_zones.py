"""The maritime zone layer (ADR-030).

Every test here DRIVES something. The rule from the build brief is that six
defects once passed green because nothing called them, so there is no test in
this file that checks a file exists, a constant is defined, or a function is
importable. Each one builds geometry, walks a track, hits a route, or asks the
store a question, and asserts on what came back.

The load-bearing cases, in the order they would hurt if they broke:

* **the two-stage membership test** — a cell index that under-covers loses every
  vessel in a zone smaller than a cell, which is most port areas;
* **crossing interpolation and censoring** — a track that starts inside a zone
  was not seen entering it, and reporting the first fix as an entry point puts a
  boundary crossing in open water;
* **the refusal to derive statutory limits** — the one thing this layer must not
  do is invent an EEZ, and a future contributor adding a plausible-looking
  polygon should fail a test rather than ship it;
* **an idle analysis says so** — the failure mode this codebase keeps
  rediscovering is a stage that computes nothing and looks healthy.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon

from maritime_isr.schemas.sources import AIS, RADAR
from maritime_isr.zones import (STATUTORY_KINDS, ZONE_KINDS, Zone, ZoneIndex,
                                build_operational_zones, cells_covering,
                                circle_polygon, contains, corridor_polygon,
                                geom_from_wkt, geom_to_wkt)
from maritime_isr.zones.analyses import (anchoring_analysis_status,
                                         detect_anchored_outside_port_limits,
                                         detect_area_visits,
                                         detect_lane_deviation,
                                         detect_maiden_visit)
from maritime_isr.zones.geometry import (bearing_deg, distance_to_m,
                                         haversine_m, polygon_from_cells)
from maritime_isr.zones.transitions import transitions_for_track

T0 = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")


# --------------------------------------------------------------------------
# fixtures — small, explicit, built here so nothing depends on a landed corpus
# --------------------------------------------------------------------------

class _Track:
    """The minimum `transitions_for_track` and the analyses need.

    Deliberately not a `BuiltTrack`: constructing one runs the Kalman filter and
    the smoother, and none of what is under test here depends on a filtered
    estimate. A test that needed the whole track engine to check a polygon test
    would be measuring the wrong thing and would be slow doing it.
    """

    def __init__(self, points, *, track_id="t1", track_key="999000001",
                 mmsi=999000001, source=AIS):
        self.track_id = track_id
        self.track_key = track_key
        self.mmsi = mmsi
        self.source = source
        self.points = points
        self.has_identity = source.key_is_identity


def _line_track(lat0, lon0, lat1, lon1, *, n=60, step_min=10, sog=10.0, **kw):
    lat = np.linspace(lat0, lat1, n)
    lon = np.linspace(lon0, lon1, n)
    cog = bearing_deg(lat0, lon0, lat1, lon1)
    df = pd.DataFrame(dict(
        lat=lat, lon=lon, sog_kn=np.full(n, float(sog)),
        cog_deg=np.full(n, cog), heading_deg=np.full(n, cog),
        ts=pd.date_range(T0, periods=n, freq=f"{step_min}min", tz="UTC"),
        quality=["ok"] * n))
    return _Track(df, **kw)


def _stationary_track(lat, lon, *, hours=9.0, sog=0.2, **kw):
    n = int(hours * 6) + 1                      # a fix every ten minutes
    df = pd.DataFrame(dict(
        lat=np.full(n, float(lat)), lon=np.full(n, float(lon)),
        sog_kn=np.full(n, float(sog)),
        cog_deg=np.zeros(n), heading_deg=np.zeros(n),
        ts=pd.date_range(T0, periods=n, freq="10min", tz="UTC"),
        quality=["ok"] * n))
    return _Track(df, **kw)


def _zone(kind, name, geom, **kw):
    return Zone(zone_id=f"zone:{kind}:{name}", kind=kind, name=name,
                wkt=geom_to_wkt(geom), authority=kw.pop("authority", "test"),
                method="test fixture", confidence=kw.pop("confidence", 0.9),
                cells=cells_covering(geom), **kw)


# ==========================================================================
# 1. geometry — true metres, not degrees
# ==========================================================================

def test_a_circle_has_the_radius_it_was_asked_for_at_every_bearing():
    """A degree-space buffer would be 10% out in longitude at this latitude.

    The whole layer's job is saying which side of a boundary something is on, so
    a circle that is 500 m wrong east-west is a boundary that is 500 m wrong.
    """
    lat, lon, r_m = 22.5, 69.7, 10_000.0
    poly = circle_polygon(lat, lon, r_m)
    for x, y in poly.exterior.coords:
        d = haversine_m(lat, lon, y, x)
        assert abs(d - r_m) < 0.02 * r_m, f"radius {d:.0f} m at ({y:.3f},{x:.3f})"


def test_a_corridor_holds_its_width_along_a_bent_centreline():
    path = [(20.0, 70.0), (19.0, 71.0), (18.0, 71.4)]
    half = 20_000.0
    poly = corridor_polygon(path, half)
    for la, lo in path:
        assert contains(poly, la, lo), "the centreline is not inside its own corridor"
    # A point one and a half half-widths abeam must be outside.
    from maritime_isr.zones.geometry import _destination
    b = bearing_deg(*path[0], *path[1])
    off = _destination(path[0][0], path[0][1], (b + 90.0) % 360.0, half * 1.6)
    assert not contains(poly, *off)


def test_polygon_from_cells_keeps_a_hole():
    """H3's own tiling gives holes for free; losing them would put every vessel
    in an excluded bay inside the zone that excludes it."""
    import h3
    outer = circle_polygon(20.0, 70.0, 60_000.0)
    inner = circle_polygon(20.0, 70.0, 20_000.0)
    from shapely.geometry import mapping
    cells = set(h3.geo_to_cells(mapping(outer), 6))
    cells -= set(h3.geo_to_cells(mapping(inner), 6))
    poly = polygon_from_cells(cells)
    assert contains(poly, 20.0, 70.45), "the ring itself is not inside"
    assert not contains(poly, 20.0, 70.0), "the hole was filled in"


def test_distance_to_is_zero_inside_and_real_metres_outside():
    poly = circle_polygon(18.0, 72.0, 10_000.0)
    assert distance_to_m(poly, 18.0, 72.0) == 0.0
    from maritime_isr.zones.geometry import _destination
    out = _destination(18.0, 72.0, 90.0, 30_000.0)
    d = distance_to_m(poly, *out)
    assert 18_000 < d < 22_000, d


# ==========================================================================
# 2. the cell index — an over-covering candidate filter, never the geometry
# ==========================================================================

def test_a_zone_smaller_than_a_cell_is_still_indexed():
    """THE case the dilation exists for.

    A res-6 cell is ~7 km across and a port area is 8-15 km, so an SPM at 2 km
    contains no cell centre at all. `geo_to_cells` alone returns the empty set,
    and a zone indexed by the empty set is a zone no vessel is ever inside — the
    exact silent failure this project keeps finding.
    """
    import h3
    from shapely.geometry import mapping
    tiny = circle_polygon(22.42, 69.64, 600.0)
    assert not set(h3.geo_to_cells(mapping(tiny), 6)), (
        "fixture no longer exercises the case — this circle now contains a "
        "res-6 cell centre, so pick a smaller one")

    z = _zone("oil_terminal", "SPM", tiny)
    assert z.cells, "polyfill returned nothing and the boundary walk did not save it"
    idx = ZoneIndex([z])
    assert idx.zones_at(22.42, 69.64), "a vessel on the mooring is not in the zone"


def test_the_index_narrows_with_the_exact_polygon_not_the_cells():
    """The candidate filter over-covers on purpose; `contains` decides.

    A position in a covering cell but outside the polygon must come back empty,
    or the layer is answering with 7 km-resolution squares while claiming to
    answer with boundaries.
    """
    from maritime_isr import h3util as tiling
    poly = circle_polygon(18.0, 72.0, 3_000.0)
    z = _zone("port_limit", "small", poly)
    idx = ZoneIndex([z])
    assert idx.zones_at(18.0, 72.0)
    # Somewhere in the dilated covering but well outside 3 km.
    from maritime_isr.zones.geometry import _destination
    out = _destination(18.0, 72.0, 45.0, 9_000.0)
    assert tiling.cell(*out, 6) in z.cells, (
        "fixture problem: pick a point the covering actually reaches")
    assert idx.zones_at(*out) == [], "the cells were used as the geometry"


def test_a_boundary_line_is_never_a_containment_candidate():
    """A line has no inside. Indexing one would make it a candidate for a test
    that can only ever answer False for every vessel afloat."""
    from shapely.geometry import LineString
    line = LineString([(68.2, 23.95), (66.85, 22.4)])
    z = _zone("imbl", "a line", line)
    idx = ZoneIndex([z])
    assert idx.zones_at(23.0, 67.5) == []
    assert idx.get(z.zone_id) is not None, "but it is still reachable by id"


# ==========================================================================
# 3. transitions — the events, and what we did and did not witness
# ==========================================================================

def test_a_crossing_is_interpolated_onto_the_boundary_not_snapped_to_a_fix():
    """A vessel at 15 knots covers 4.6 km between ten-minute fixes.

    Recording the first inside fix as the entry point puts the crossing
    kilometres inside the zone, and every bearing computed from it is a bearing
    from the wrong place.
    """
    poly = circle_polygon(19.0, 72.0, 20_000.0)
    idx = ZoneIndex([_zone("port_limit", "P", poly)])
    tr = _line_track(18.5, 71.5, 19.5, 72.5, n=40, step_min=10, sog=15.0)
    rows = transitions_for_track(tr, idx)
    assert len(rows) == 1, rows
    r = rows[0]
    d = haversine_m(r["entry_lat"], r["entry_lon"], 19.0, 72.0)
    assert abs(d - 20_000.0) < 1_500.0, (
        f"entry recorded {d:.0f} m from the centre of a 20 km zone")
    assert not r["entry_censored"] and not r["exit_censored"]
    assert 0.0 <= r["entry_bearing_deg"] <= 360.0


def test_a_track_that_starts_inside_reports_a_censored_entry():
    """We did not see her come in, so we must not say where she came from."""
    poly = circle_polygon(19.0, 72.0, 50_000.0)
    idx = ZoneIndex([_zone("port_limit", "P", poly)])
    tr = _line_track(19.0, 72.0, 19.6, 72.7, n=40, step_min=10)
    rows = transitions_for_track(tr, idx)
    assert rows and rows[0]["entry_censored"] is True
    assert rows[0]["entry_bearing_deg"] is None, (
        "a bearing was reported for a crossing nobody witnessed")


def test_still_inside_at_the_last_fix_is_an_open_interval_not_an_exit():
    poly = circle_polygon(19.0, 72.0, 60_000.0)
    idx = ZoneIndex([_zone("port_limit", "P", poly)])
    tr = _line_track(18.4, 71.4, 19.0, 72.0, n=40, step_min=10)
    rows = transitions_for_track(tr, idx)
    assert rows and rows[0]["t_exit"] is None
    assert rows[0]["exit_censored"] is True


def test_a_brief_clip_of_a_corner_is_not_an_event():
    """The four large areas would otherwise emit an event every time a track's
    noise crossed a line."""
    poly = circle_polygon(19.0, 72.0, 3_000.0)
    idx = ZoneIndex([_zone("port_limit", "P", poly)])
    tr = _line_track(18.9, 71.9, 19.1, 72.1, n=60, step_min=1, sog=14.0)
    assert transitions_for_track(tr, idx) == []


def test_a_radar_track_produces_transitions_and_carries_no_mmsi():
    """A zone crossing is a fact about a position history, not about an
    identity — a contact nobody can name entering an area is exactly as much of
    an event as a named hull doing it."""
    poly = circle_polygon(19.0, 72.0, 25_000.0)
    idx = ZoneIndex([_zone("sensitive_area", "S", poly)])
    tr = _line_track(18.5, 71.5, 19.5, 72.5, n=40, step_min=10,
                     source=RADAR, mmsi=None, track_key="SYN-MUM:0007")
    rows = transitions_for_track(tr, idx)
    assert rows, "no transition from a radar track"
    assert rows[0]["mmsi"] is None
    assert rows[0]["track_source"] == "radar"
    assert rows[0]["track_key"] == "SYN-MUM:0007"


# ==========================================================================
# 4. the refusal — statutory limits are not derived, and must not become so
# ==========================================================================

def test_the_operational_set_contains_no_statutory_limit():
    """The one thing this layer must not do is invent a boundary.

    A future contributor adding a plausible-looking EEZ polygon to
    `build_operational_zones` should fail here rather than ship it. The kinds
    exist and the connector fills them; nothing computes them.
    """
    kinds = {z.kind for z in build_operational_zones()}
    assert not (kinds & STATUTORY_KINDS), (
        f"the operational set now derives {sorted(kinds & STATUTORY_KINDS)} — "
        f"see zones/derive.py for why it must not")
    assert kinds <= set(ZONE_KINDS)


def test_every_operational_zone_says_what_it_is_worth():
    """Three kinds of geometry share one table and a consumer that cannot tell
    them apart will render a 10 km circle as a declared limit."""
    for z in build_operational_zones():
        assert z.authority, z.name
        assert z.method, z.name
        assert 0.0 < z.confidence <= 1.0, (z.name, z.confidence)
        if z.kind in ("port_limit", "shipping_lane"):
            assert "NOT A DECLARED LIMIT" in z.note.upper() \
                or "NOT AN IMO ROUTEING MEASURE" in z.note.upper(), (
                    f"{z.name} does not disclaim what it is not")


def test_the_anchoring_analysis_reports_that_it_is_idle_rather_than_clean():
    """An analysis that cannot run must say so.

    Returning an empty list is indistinguishable from having looked and found
    nothing, and this codebase has now found that same defect under five
    different names.
    """
    idx = ZoneIndex(build_operational_zones())
    ok, why = anchoring_analysis_status(idx)
    assert ok is False
    assert "IDLE" in why and "territorial_sea" in why
    assert "ingest zones" in why, "the reason does not say how to fix it"


# ==========================================================================
# 5. the analyses — each driven, each with its own decoy
# ==========================================================================

def _graph(tmp_path):
    from maritime_isr.graph import GraphStore
    return GraphStore(tmp_path / "g.sqlite")


def _visit(zone_id, kind, mmsi, day, name="Z"):
    t = T0 + pd.Timedelta(days=day)
    return dict(transition_id=f"x{zone_id}{day}", zone_id=zone_id,
                zone_kind=kind, zone_name=name, track_id="t", track_key=str(mmsi),
                track_source="ais", mmsi=mmsi, t_enter=t,
                t_exit=t + pd.Timedelta(hours=6), dwell_min=360.0,
                entry_lat=19.0, entry_lon=72.0, entry_bearing_deg=90.0,
                exit_lat=19.1, exit_lon=72.1, exit_bearing_deg=270.0,
                min_sog_kn=0.1, mean_sog_kn=0.5, n_fixes=36,
                entry_censored=False, exit_censored=False)


def test_maiden_visit_fires_on_a_settled_hull_and_not_on_a_debut(tmp_path):
    """Both halves in one test, because the rule IS the contrast.

    Without the history qualifier this fires on every vessel's first appearance
    anywhere, which is a list of the fleet rather than a finding.
    """
    store = _graph(tmp_path)
    try:
        settled = [_visit("z:a", "port_limit", 999000001, 0),
                   _visit("z:b", "port_limit", 999000001, 2),
                   _visit("z:c", "anchorage", 999000001, 4),
                   _visit("z:d", "port_limit", 999000001, 9)]   # the fourth
        newcomer = [_visit("z:d", "port_limit", 999000002, 9)]  # her debut
        fired = detect_maiden_visit(store, settled + newcomer, source_ref="t")
        assert len(fired) == 1, f"expected exactly the settled hull, got {fired}"
        a = store.alerts()[0]
        assert a["props"]["prior_zones"] == 3
        assert a["props"]["zone_id"] == "z:d"
    finally:
        store.close()


def test_maiden_visit_declines_to_claim_anything_about_a_radar_contact(tmp_path):
    """"She has never been here before" is a statement about a hull. A station
    track number is recycled in minutes and cannot carry it."""
    store = _graph(tmp_path)
    try:
        rows = [dict(_visit(f"z:{i}", "port_limit", None, i), mmsi=None,
                     track_key="SYN-MUM:0007") for i in range(5)]
        assert detect_maiden_visit(store, rows, source_ref="t") == []
    finally:
        store.close()


def test_lane_deviation_fires_off_route_and_stays_quiet_on_it(tmp_path):
    store = _graph(tmp_path)
    try:
        lane = corridor_polygon([(20.0, 70.0), (18.0, 71.0), (16.0, 72.0)],
                                25_000.0)
        idx = ZoneIndex([_zone("shipping_lane", "L", lane)])

        on_lane = _line_track(20.0, 70.0, 16.0, 72.0, n=80, step_min=30,
                              sog=12.0)
        assert detect_lane_deviation(store, [on_lane], idx, source_ref="t") == []

        off = _line_track(20.0, 68.4, 16.0, 68.9, n=80, step_min=30, sog=12.0,
                          mmsi=999000003, track_key="999000003", track_id="t2")
        fired = detect_lane_deviation(store, [off], idx, source_ref="t")
        assert fired, "a day-long passage 150 km off every corridor did not fire"
        a = [x for x in store.alerts() if x["anomaly_type"] == "lane_deviation"][0]
        assert a["props"]["distance_km"] > 60.0
    finally:
        store.close()


def test_lane_deviation_ignores_a_vessel_that_is_not_making_way(tmp_path):
    """Drifting off-route is the loitering rule's finding, not this one's, and
    double-counting it would put the same vessel in the queue twice."""
    store = _graph(tmp_path)
    try:
        lane = corridor_polygon([(20.0, 70.0), (16.0, 72.0)], 25_000.0)
        idx = ZoneIndex([_zone("shipping_lane", "L", lane)])
        drifting = _stationary_track(18.0, 68.0, hours=20.0, sog=0.4)
        assert detect_lane_deviation(store, [drifting], idx, source_ref="t") == []
    finally:
        store.close()


def test_anchoring_outside_limits_fires_only_inside_a_territorial_sea(tmp_path):
    """The rule's first condition, driven with a real territorial sea supplied.

    This is what proves the analysis WORKS while the layer ships without a
    territorial sea: the code path runs here, with geometry handed to it exactly
    as the connector would.
    """
    store = _graph(tmp_path)
    try:
        ts = circle_polygon(17.3, 73.1, 40_000.0)
        port = circle_polygon(17.3, 73.1, 8_000.0)
        idx = ZoneIndex([_zone("territorial_sea", "TS", ts),
                         _zone("port_limit", "P", port)])
        ok, _ = anchoring_analysis_status(idx)
        assert ok is True

        # 25 km out: inside the territorial sea, outside the port area.
        from maritime_isr.zones.geometry import _destination
        pos = _destination(17.3, 73.1, 250.0, 25_000.0)
        stopped = _stationary_track(*pos, hours=9.0, sog=0.2)
        fired = detect_anchored_outside_port_limits(store, [stopped], idx,
                                                    source_ref="t")
        assert fired, "nine hours stopped in territorial waters did not fire"
        a = store.alerts()[0]
        assert a["props"]["hours"] >= 6.0
        assert a["props"]["nearest_facility"]
    finally:
        store.close()


def test_anchoring_outside_limits_spares_a_vessel_in_a_designated_anchorage(tmp_path):
    """The 2026-08-01 loitering defect, reached from a different direction.

    Without the anchorage layer this fires on every merchant queueing for a
    berth — which is the argument for these areas being data rather than four
    constants.
    """
    store = _graph(tmp_path)
    try:
        ts = circle_polygon(22.9, 70.2, 40_000.0)
        anch = circle_polygon(22.8, 70.0, 12_000.0)
        idx = ZoneIndex([_zone("territorial_sea", "TS", ts),
                         _zone("anchorage", "A", anch)])
        waiting = _stationary_track(22.8, 70.0, hours=11.0, sog=0.3)
        assert detect_anchored_outside_port_limits(
            store, [waiting], idx, source_ref="t") == []
    finally:
        store.close()


def test_anchoring_outside_limits_spares_a_vessel_on_the_high_seas(tmp_path):
    """Outside any territorial sea, "anchored outside port limits" asserts a
    jurisdiction the geometry does not support."""
    store = _graph(tmp_path)
    try:
        ts = circle_polygon(21.6, 69.6, 22_000.0)
        idx = ZoneIndex([_zone("territorial_sea", "TS", ts)])
        from maritime_isr.zones.geometry import _destination
        far = _destination(21.6, 69.6, 210.0, 74_000.0)
        stopped = _stationary_track(*far, hours=9.0, sog=0.2)
        assert detect_anchored_outside_port_limits(
            store, [stopped], idx, source_ref="t") == []
    finally:
        store.close()


def test_area_visit_is_a_window_overlap_not_a_containment():
    """A vessel that entered on Monday and left on Friday was in the box on
    Tuesday, and a query for Tuesday that missed her would be wrong in the way
    that matters most."""
    rows = [_visit("z:a", "geofence", 999000001, 0)]
    rows[0]["t_exit"] = T0 + pd.Timedelta(days=4)
    got = detect_area_visits(rows, start=T0 + pd.Timedelta(days=2),
                             end=T0 + pd.Timedelta(days=3))
    assert len(got) == 1
    assert detect_area_visits(rows, start=T0 + pd.Timedelta(days=9)) == []


# ==========================================================================
# 6. the connector — the only door statutory geometry comes through
# ==========================================================================

def test_the_connector_maps_marine_regions_vocabulary_to_kinds():
    from maritime_isr.ingest.zones import kind_from_properties
    assert kind_from_properties({"POL_TYPE": "200NM"}) == "eez"
    assert kind_from_properties({"POL_TYPE": "12NM"}) == "territorial_sea"
    assert kind_from_properties({"POL_TYPE": "24NM"}) == "contiguous_zone"
    # An explicit kind written by whoever made the file wins over inference.
    assert kind_from_properties({"POL_TYPE": "200NM", "kind": "geofence"}) \
        == "geofence"
    # And a value it does not recognise is NOT quietly filed as an EEZ.
    assert kind_from_properties({"POL_TYPE": "Overlapping claim"}) is None


def test_the_connector_skips_what_it_cannot_classify_and_says_so():
    from maritime_isr.ingest.zones import conform_features
    feats = [
        {"properties": {"POL_TYPE": "12NM", "GEONAME": "Indian TS"},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[70, 20], [71, 20], [71, 21], [70, 20]]]}},
        {"properties": {"mystery": "yes"},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[70, 20], [71, 20], [71, 21], [70, 20]]]}},
    ]
    zones, skipped = conform_features(feats, default_kind=None,
                                      authority="test", source_ref="f.geojson",
                                      confidence=0.9)
    assert len(zones) == 1 and zones[0].kind == "territorial_sea"
    assert len(skipped) == 1 and "cannot determine kind" in skipped[0]


def test_an_imported_territorial_sea_makes_the_idle_analysis_run(tmp_path):
    """End to end, through the real connector, with a real file on disk.

    The proof that "idle" means *waiting for geometry* rather than *broken*:
    write a GeoJSON, land it, and the analysis that reported itself idle a
    moment ago now runs and fires — with no code change anywhere.
    """
    import maritime_isr.config as cfg_mod
    from maritime_isr.ingest import zones as zconn
    from maritime_isr.zones import load_zones

    # Point the whole landing layer at a temp root so the real corpus is
    # untouched; this test writes.
    root = tmp_path / "data"
    old = cfg_mod.cfg.data_root
    object.__setattr__(cfg_mod.cfg, "data_root", root)
    try:
        ring = list(circle_polygon(17.3, 73.1, 40_000.0).exterior.coords)
        doc = {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "properties": {"POL_TYPE": "12NM", "GEONAME": "Test TS"},
            "geometry": {"type": "Polygon", "coordinates": [[list(c) for c in ring]]},
        }]}
        path = tmp_path / "ts.geojson"
        path.write_text(json.dumps(doc))

        res = zconn.run(path)
        assert res["zones"] == 1, res
        assert not res["skipped"], res["skipped"]

        idx = ZoneIndex(load_zones())
        ok, why = anchoring_analysis_status(idx)
        assert ok is True, why

        store = _graph(tmp_path)
        try:
            from maritime_isr.zones.geometry import _destination
            pos = _destination(17.3, 73.1, 250.0, 25_000.0)
            stopped = _stationary_track(*pos, hours=9.0, sog=0.2)
            assert detect_anchored_outside_port_limits(
                store, [stopped], idx, source_ref="t"), (
                "the analysis is still silent with a territorial sea loaded")
        finally:
            store.close()
    finally:
        object.__setattr__(cfg_mod.cfg, "data_root", old)


# ==========================================================================
# 7. the gazetteer gap, measured
# ==========================================================================

def test_the_gazetteer_gained_the_west_coast_and_the_gain_is_measurable():
    from maritime_isr.ports import GAZETTEER_V1_NAMES, PORTS, gazetteer_recall

    assert GAZETTEER_V1_NAMES < set(PORTS), "the v1 list is not a subset any more"
    for name in ("Mormugao", "Okha", "Dwarka", "Ratnagiri", "New Mangalore"):
        assert name in PORTS, f"{name} is still missing from the gazetteer"

    # Positions AT the newly added facilities: nameable now, nameless before.
    stops = [PORTS[n] for n in ("Mormugao", "Okha", "Dwarka", "Ratnagiri")]
    rec = gazetteer_recall(stops)
    assert rec["named_before"] == 0
    assert rec["named_after"] == len(stops)
    assert rec["gained"] == len(stops)
    assert rec["n_ports_after"] > rec["n_ports_before"]


def test_every_new_port_floats():
    """A berth is on the coastline and a 1 km mask calls that land; the
    reference point has to be water a ship can be at, or the map draws vessels
    in the middle of Gujarat."""
    globe = pytest.importorskip("global_land_mask").globe
    from maritime_isr.ports import GAZETTEER_V1_NAMES, PORTS
    added = {n: p for n, p in PORTS.items() if n not in GAZETTEER_V1_NAMES}
    assert added, "no ports were added"
    on_land = [(n, p) for n, p in added.items() if globe.is_land(*p)]
    assert not on_land, f"new port reference points on land: {on_land}"


# ==========================================================================
# 8. the serving layer — a drawn box answers, and a standing zone is protected
# ==========================================================================

def _client():
    from fastapi.testclient import TestClient

    from maritime_isr.api.app import create_app
    from maritime_isr.api.settings import settings
    c = TestClient(create_app())
    c.headers.update({"X-API-Token": settings.token})
    return c


def test_the_zone_endpoint_names_the_kinds_it_does_not_have():
    """A map that simply does not draw an EEZ looks identical to one whose EEZ
    is empty. Naming the gap is the difference."""
    r = _client().get("/api/zones")
    assert r.status_code == 200, r.text
    body = r.json()
    if not body["items"]:
        pytest.skip("no landed zone layer — run `maritime-isr zones build`")
    assert set(body["missing_kinds"]) == set(STATUTORY_KINDS)
    assert "will not derive" in (body.get("note") or "")
    for z in body["items"]:
        assert z["authority"] and z["method"]
        assert z["geometry"]["type"] in ("Polygon", "MultiPolygon",
                                         "LineString", "MultiLineString")
    orders = [z["render_order"] for z in body["items"]]
    assert orders == sorted(orders), "zones are not ordered back to front"


def test_a_drawn_area_is_saved_queried_and_deleted_like_any_other_zone():
    """The sentence this build earns, driven end to end through the API.

    Also the requirement that a drawn area and a standing zone are the same
    kind of object: it comes back from the same `/zones` route, with the same
    fields, and is queried by the same `/vessels` route.
    """
    c = _client()
    if not c.get("/api/zones").json()["items"]:
        pytest.skip("no landed zone layer")
    box = {"type": "Polygon",
           "coordinates": [[[72.2, 18.4], [72.9, 18.4], [72.9, 19.0],
                            [72.2, 19.0], [72.2, 18.4]]]}
    made = c.post("/api/geofences",
                  json={"name": "pytest box", "geometry": box})
    assert made.status_code == 200, made.text
    zid = made.json()["zone_id"]
    try:
        listed = c.get("/api/zones", params={"kind": "geofence"}).json()
        assert any(z["zone_id"] == zid for z in listed["items"])

        v = c.get(f"/api/zones/{zid}/vessels", params={"limit": 50})
        assert v.status_code == 200, v.text
        body = v.json()
        # `computed` or `computed-empty` — either is an ANSWER. What must never
        # come back for a freshly drawn box is a bare empty list with no basis.
        assert body["basis"].startswith("computed"), body["basis"]
        assert body["note"], "an on-demand answer did not say it was on-demand"
    finally:
        c.delete(f"/api/geofences/{zid}")
    after = c.get("/api/zones", params={"kind": "geofence"}).json()
    assert not any(z["zone_id"] == zid for z in after["items"])


def test_a_standing_zone_cannot_be_deleted_through_the_geofence_route():
    """The conformed layer is landed data. A UI button that could erase a
    boundary an analysis depends on would leave stored transitions pointing at
    nothing."""
    c = _client()
    standing = [z for z in c.get("/api/zones").json()["items"]
                if z["authority"] != "operator"]
    if not standing:
        pytest.skip("no landed zone layer")
    r = c.delete(f"/api/geofences/{standing[0]['zone_id']}")
    assert r.status_code == 403, r.text
    assert "not an operator-drawn one" in r.json()["detail"]


def test_an_invalid_drawn_geometry_is_refused_with_a_readable_reason():
    c = _client()
    r = c.post("/api/geofences", json={"name": "", "geometry": {
        "type": "Polygon", "coordinates": [[[72, 18], [73, 18], [73, 19],
                                            [72, 18]]]}})
    assert r.status_code == 400
    assert "name" in r.json()["detail"]


# ==========================================================================
# 9. the migration — the four circles moved without changing behaviour
# ==========================================================================

def test_the_four_sensitive_areas_are_now_zone_rows_and_still_the_same_four():
    """A migration that changed detection behaviour at the same time as it
    moved the data would make any regression impossible to attribute."""
    from maritime_isr.anomaly.library import SENSITIVE_ZONES
    from maritime_isr.zones.derive import SENSITIVE_AREAS

    assert len(SENSITIVE_ZONES) == 4
    assert {z["name"] for z in SENSITIVE_ZONES} == set(SENSITIVE_AREAS)
    for z in SENSITIVE_ZONES:
        lat, lon, r = SENSITIVE_AREAS[z["name"]]
        assert (z["lat"], z["lon"], z["radius_km"]) == (lat, lon, r)

    kinds = {z.kind for z in build_operational_zones()}
    assert "sensitive_area" in kinds


def test_loitering_watches_an_operator_drawn_geofence_when_given_the_index(tmp_path):
    """"Geofencing from a stub into a real feature", made true at the one place
    it is easiest to fake: the drawn area is watched by the same rule, with the
    same threshold, as the four that were compiled in."""
    from maritime_isr.anomaly.library import detect_sensitive_loitering

    store = _graph(tmp_path)
    try:
        drawn = circle_polygon(16.0, 67.0, 30_000.0)   # nowhere near the four
        idx = ZoneIndex([_zone("geofence", "operator box", drawn,
                               authority="operator", confidence=1.0)])
        tr = _stationary_track(16.0, 67.0, hours=6.0, sog=0.3)
        assert detect_sensitive_loitering(store, [tr], source_ref="t") == [], (
            "fixture problem: this position is already inside a compiled circle")
        fired = detect_sensitive_loitering(store, [tr], source_ref="t",
                                           index=idx)
        assert fired, "an operator-drawn geofence is not watched"
        a = store.alerts()[0]
        assert a["props"]["zone_kind"] == "geofence"
        assert a["props"]["zone_id"].startswith("zone:geofence:")
    finally:
        store.close()


def test_the_graph_ontology_admits_a_zone_and_its_edges():
    """`loiter-in-zone` has been emitted since Phase 5 against a `zone:<name>`
    destination that was never a registered node type. It validated only because
    nothing checked."""
    from maritime_isr.graph.ontology import (EDGE_TYPES_V1, NODE_TYPES_V1,
                                             validate_edge)
    assert "zone" in NODE_TYPES_V1
    for etype in ("entered-zone", "loiter-in-zone", "deviated-from-lane",
                  "anchored-outside-limits"):
        assert etype in EDGE_TYPES_V1, etype
        validate_edge(etype, "vessel", "zone", EDGE_TYPES_V1)
        validate_edge(etype, "contact", "zone", EDGE_TYPES_V1)


# ==========================================================================
# 10. the format the publisher actually ships
# ==========================================================================

def _fake_boundary_zip(tmp_path, *, prj: str, pol_type: str = "12NM"):
    """A real shapefile, zipped the way Marine Regions ships one."""
    import zipfile

    shapefile = pytest.importorskip("shapefile")
    base = tmp_path / "ts"
    w = shapefile.Writer(str(base))
    w.field("MRGID", "N")
    w.field("GEONAME", "C", 60)
    w.field("POL_TYPE", "C", 20)
    ring = [(x, y) for x, y in circle_polygon(17.3, 73.1, 40_000.0).exterior.coords]
    w.poly([ring])
    w.record(1234, "Indian 12 NM", pol_type)
    w.close()
    base.with_suffix(".prj").write_text(prj)
    zp = tmp_path / "World_12NM_v4_20231025.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for ext in (".shp", ".dbf", ".shx", ".prj"):
            z.write(base.with_suffix(ext), f"ts{ext}")
    return zp


def test_the_connector_reads_a_zipped_shapefile(tmp_path):
    """**Marine Regions ships shapefile, GeoPackage and KML — not GeoJSON.**

    The connector was GeoJSON-only on its first pass, which made it a door
    nobody could walk through: the operator downloaded exactly the file the
    docstring told them to and the connector could not open it. Read with
    `pyshp`, which is pure Python — no GDAL, so `pip install -e .` on a Windows
    laptop stays a download rather than a compiler problem.
    """
    from maritime_isr.ingest.zones import (_features_from_shapefile,
                                           conform_features)
    zp = _fake_boundary_zip(tmp_path, prj='GEOGCS["GCS_WGS_1984"]')
    feats = _features_from_shapefile(zp.read_bytes(), zp.name)
    assert len(feats) == 1
    assert feats[0]["properties"]["POL_TYPE"] == "12NM"

    zones, skipped = conform_features(feats, default_kind=None, authority="t",
                                      source_ref=zp.name, confidence=0.9)
    assert not skipped, skipped
    assert [z.kind for z in zones] == ["territorial_sea"], (
        "POL_TYPE 12NM did not map to a territorial sea")
    assert contains(geom_from_wkt(zones[0].wkt), 17.3, 73.1)


def test_the_connector_refuses_a_projected_shapefile(tmp_path):
    """Landing metres as degrees would put the Indian territorial sea in the
    Gulf of Guinea while every row looked perfectly well-formed.

    Worse than useless: a partially-overlapping projection lands SOME rows in
    plausible positions, which is the silent-corruption shape this project
    exists to engineer against. Cheap check, expensive failure.
    """
    from maritime_isr.ingest.zones import _features_from_shapefile
    zp = _fake_boundary_zip(
        tmp_path, prj='PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984"]]')
    with pytest.raises(SystemExit) as e:
        _features_from_shapefile(zp.read_bytes(), zp.name)
    assert "PROJECTED" in str(e.value)
    assert "EPSG:4326" in str(e.value), "the refusal does not say how to fix it"


def test_a_geopackage_is_refused_with_the_format_that_does_work(tmp_path):
    """A wrong-format file must not produce a JSON parse error and nothing else.

    The operator has just downloaded 50 MB from a page offering four formats;
    the message has to name the one that works.
    """
    from maritime_isr.ingest import zones as zconn
    p = tmp_path / "World_12NM_v4.gpkg"
    p.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    with pytest.raises(SystemExit) as e:
        zconn.run(p)
    msg = str(e.value)
    assert "Shapefile" in msg and "GeoPackage" in msg
