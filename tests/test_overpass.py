"""Tests for the SAR imaging-opportunity geometry (maritime_isr/overpass.py).

The claims this module makes are geometric, so the tests are built around
hand-constructed geometry with a known answer rather than around fixtures that
merely re-state the implementation. The important ones are the tier boundaries:
a `confirmed` row asserts that a vessel was necessarily inside an imaged
rectangle, and that assertion has to survive being wrong.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr import overpass as ov


UTC = timezone.utc
T0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def _gap(*, off=(18.0, 68.0), on=(18.0, 68.5), hours=10.0,
         event_id="gap-1", vessel_id="v-1", synthetic=False):
    """A gap that went dark at `off` and resurfaced at `on`, `hours` later."""
    return {
        "event_id": event_id,
        "vessel_id": vessel_id,
        "start_time": T0,
        "end_time": T0 + timedelta(hours=hours),
        "gap_off_lat": off[0] if off else None,
        "gap_off_lon": off[1] if off else None,
        "gap_on_lat": on[0] if on else None,
        "gap_on_lon": on[1] if on else None,
        "gap_duration_hours": hours,
        "gfw_intentional_disabling": 1,
        "is_synthetic": synthetic,
    }


def _box(lat_c, lon_c, half_deg):
    """A square footprint in WKT, centred on (lat_c, lon_c)."""
    la0, la1 = lat_c - half_deg, lat_c + half_deg
    lo0, lo1 = lon_c - half_deg, lon_c + half_deg
    return (f"POLYGON (({lo0} {la0}, {lo1} {la0}, {lo1} {la1}, "
            f"{lo0} {la1}, {lo0} {la0}))")


def _scene(footprint, *, at_hours=5.0, scene_id="S1A_TEST", status="cataloged"):
    return {
        "scene_id": scene_id,
        "footprint_wkt": footprint,
        "acquired_at": T0 + timedelta(hours=at_hours),
        "orbit_direction": "ASCENDING",
        "relative_orbit": 42,
        "mode": "IW",
        "polarizations": "VV+VH",
        "status": status,
    }


# ---------------------------------------------------------------------------
# the three tiers
# ---------------------------------------------------------------------------

def test_huge_footprint_contains_the_whole_reachable_region():
    """A footprint far larger than the reachable lens is a confirmed pass."""
    gap = _gap()
    # 4 degrees half-width ~ 440 km; the lens over a 10 h gap at 20 kn is far
    # smaller, so containment must be total.
    r = ov.assess_pass(gap, _scene(_box(18.0, 68.25, 4.0)))
    assert r["tier"] == "confirmed"
    assert r["coverage_fraction"] == pytest.approx(1.0, abs=1e-3)
    assert r["geometry_basis"] == "lens"


def test_footprint_far_away_yields_no_overlap():
    gap = _gap()
    r = ov.assess_pass(gap, _scene(_box(30.0, 80.0, 1.0)))
    assert r["tier"] == "none"
    assert r["coverage_fraction"] == 0.0


def test_footprint_clipping_the_region_is_partial():
    """A footprint whose edge cuts through the lens covers part of it."""
    gap = _gap(hours=20.0)          # a wide lens
    full = ov.assess_pass(gap, _scene(_box(18.0, 68.25, 4.0)))
    assert full["tier"] == "confirmed"

    # A half-plane-ish box starting at the lens midpoint and running east.
    clipped = ov.assess_pass(gap, _scene(_box(18.0, 68.25 + 4.0, 4.0)))
    assert clipped["tier"] == "partial"
    assert 0.0 < clipped["coverage_fraction"] < 1.0
    # And the covered area must be a real subset, not a rounding artefact.
    assert clipped["covered_area_km2"] < full["reachable_area_km2"]


def test_partial_coverage_is_reported_as_area_not_probability():
    """The row carries areas and a fraction; nothing calls it a likelihood."""
    gap = _gap(hours=20.0)
    r = ov.assess_pass(gap, _scene(_box(18.0, 72.25, 4.0)))
    if r["tier"] == "partial":
        assert "coverage_fraction" in r
        assert r["covered_area_km2"] <= r["reachable_area_km2"] + 1e-6
        assert "probab" not in ov._statement(r).lower()
        assert "likel" not in ov._statement(r).lower()


# ---------------------------------------------------------------------------
# the time window
# ---------------------------------------------------------------------------

def test_scene_before_the_gap_is_not_an_opportunity():
    assert ov.assess_pass(_gap(), _scene(_box(18.0, 68.25, 4.0),
                                         at_hours=-1.0)) is None


def test_scene_after_the_gap_is_not_an_opportunity():
    assert ov.assess_pass(_gap(hours=10.0),
                          _scene(_box(18.0, 68.25, 4.0), at_hours=11.0)) is None


def test_scene_at_the_instant_the_gap_opens_is_assessed():
    """A zero-radius disc must not silently drop the scene (MIN_RADIUS_M)."""
    r = ov.assess_pass(_gap(), _scene(_box(18.0, 68.0, 4.0), at_hours=0.0))
    assert r is not None
    assert r["tier"] == "confirmed"
    assert r["hours_into_gap"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# the reachable region itself
# ---------------------------------------------------------------------------

def test_region_is_widest_in_the_middle_of_the_gap():
    """The lens grows from the off position and shrinks toward the on one."""
    gap = _gap(hours=12.0)
    fp = _box(18.0, 68.25, 6.0)
    areas = [ov.assess_pass(gap, _scene(fp, at_hours=h))["reachable_area_km2"]
             for h in (0.5, 3.0, 6.0, 9.0, 11.5)]
    assert areas[2] == max(areas)
    assert areas[0] < areas[1] < areas[2]
    assert areas[4] < areas[3] < areas[2]


def test_faster_assumed_speed_grows_the_region():
    """Generosity in v_max must make containment harder, never easier."""
    gap = _gap(hours=10.0)
    fp = _box(18.0, 68.25, 6.0)
    slow = ov.assess_pass(gap, _scene(fp), v_max_kn=10.0)
    fast = ov.assess_pass(gap, _scene(fp), v_max_kn=30.0)
    assert fast["reachable_area_km2"] > slow["reachable_area_km2"]


def test_missing_on_position_falls_back_to_a_forward_cone():
    gap = _gap(on=None)
    r = ov.assess_pass(gap, _scene(_box(18.0, 68.0, 6.0)))
    assert r["geometry_basis"] == "forward_cone"
    assert r["tier"] in ("confirmed", "partial")


def test_missing_off_position_falls_back_to_a_backward_cone():
    gap = _gap(off=None)
    r = ov.assess_pass(gap, _scene(_box(18.0, 68.5, 6.0)))
    assert r["geometry_basis"] == "backward_cone"


def test_no_positions_at_all_is_unknown_not_none():
    """Absence of evidence and inability to look are different rows (ADR-021)."""
    r = ov.assess_pass(_gap(off=None, on=None), _scene(_box(18.0, 68.0, 4.0)))
    assert r["tier"] == "unknown"
    assert r["geometry_basis"] == "no_position"


def test_unparseable_footprint_is_unknown_not_none():
    r = ov.assess_pass(_gap(), _scene("not a polygon"))
    assert r["tier"] == "unknown"
    assert "footprint" in r["reason"]


# ---------------------------------------------------------------------------
# the impossible-speed case — a spoofing tell, not a bad row (CLAUDE.md §6)
# ---------------------------------------------------------------------------

def test_endpoints_further_apart_than_vmax_allows_are_flagged_not_dropped():
    # ~5 degrees of longitude in 1 hour is several hundred knots.
    gap = _gap(off=(18.0, 68.0), on=(18.0, 73.0), hours=1.0)
    r = ov.assess_pass(gap, _scene(_box(18.0, 70.5, 6.0), at_hours=0.5))
    assert r is not None
    assert r["implied_speed_exceeds_vmax"] is True
    assert r["implied_speed_kn"] > ov.V_MAX_DEFAULT_KN
    # Crucially the geometry still resolves rather than returning empty.
    assert r["tier"] in ("confirmed", "partial")
    assert r["reachable_area_km2"] > 0


def test_normal_speed_gap_is_not_flagged():
    gap = _gap(off=(18.0, 68.0), on=(18.0, 68.2), hours=10.0)
    r = ov.assess_pass(gap, _scene(_box(18.0, 68.1, 6.0)))
    assert r["implied_speed_exceeds_vmax"] is False


def test_effective_speed_headroom_keeps_the_discs_overlapping():
    """At exactly the implied speed the discs would touch at a point."""
    v_ms, implied, exceeded = ov.effective_v_ms(
        off=(0.0, 0.0), on=(100_000.0, 0.0), duration_s=3600.0)
    assert exceeded is True
    assert v_ms / ov.KN_TO_MS > implied


# ---------------------------------------------------------------------------
# a gap nobody photographed still produces a row
# ---------------------------------------------------------------------------

def test_gap_with_no_pass_lands_an_explicit_none_row():
    gap = _gap()
    rows = ov.opportunities_for_gap(gap, [_scene(_box(30.0, 80.0, 1.0))])
    assert len(rows) == 1
    assert rows[0]["tier"] == "none"
    assert rows[0]["scene_id"] == ""
    assert "no Sentinel-1 pass" in rows[0]["reason"].lower() or \
           "no sentinel-1 pass" in rows[0]["reason"].lower()


def test_opportunities_are_ordered_best_tier_first():
    gap = _gap(hours=20.0)
    scenes = [
        _scene(_box(18.0, 68.25 + 4.0, 4.0), scene_id="PARTIAL"),
        _scene(_box(18.0, 68.25, 5.0), scene_id="CONFIRMED"),
    ]
    rows = ov.opportunities_for_gap(gap, scenes)
    assert rows[0]["scene_id"] == "CONFIRMED"
    assert rows[0]["tier"] == "confirmed"


# ---------------------------------------------------------------------------
# what a row is allowed to say
# ---------------------------------------------------------------------------

def test_confirmed_statement_refuses_to_claim_a_detection():
    gap = _gap()
    r = ov.assess_pass(gap, _scene(_box(18.0, 68.25, 4.0)))
    s = ov._statement(r)
    assert "no detection is claimed" in s.lower()
    for forbidden in ("detected", "dark vessel", "confirmed dark"):
        assert forbidden not in s.lower().replace("no detection is claimed", "")


def test_scene_status_records_whether_pixels_exist():
    gap = _gap()
    fp = _box(18.0, 68.25, 4.0)
    assert ov.assess_pass(gap, _scene(fp, status="cataloged"))["scene_has_pixels"] is False
    assert ov.assess_pass(gap, _scene(fp, status="raw"))["scene_has_pixels"] is True


def test_confidence_never_reaches_certainty():
    """v_max is assumed, so even a containment is not confidence 1.0."""
    assert 0 < ov.CONFIDENCE_BY_TIER["confirmed"] < 1.0
    assert ov.CONFIDENCE_BY_TIER["partial"] < ov.CONFIDENCE_BY_TIER["confirmed"]
    assert ov.CONFIDENCE_BY_TIER["none"] is None


# ---------------------------------------------------------------------------
# characterisation — what tier to expect, and why
# ---------------------------------------------------------------------------

def test_confirmed_containment_needs_a_short_gap_or_a_pass_near_its_edge():
    """The property that decides what this module can actually claim.

    A Sentinel-1 IW footprint is roughly 250 km square, ~62,500 km². At 20 kn
    the reachable region reaches that size about six hours into a gap, so:

      * a **short** gap can be contained outright, and
      * a **long** gap can only be contained by a pass near one of its ends,
        where the vessel has had little time to move.

    Mid-gap passes on long gaps therefore land `partial` with a small fraction.
    That is a fact about orbital timing and ship speed, not a tuning choice,
    and it is pinned here so a future change to `V_MAX_DEFAULT_KN` or the tier
    thresholds cannot quietly turn thin coverage into confident claims.
    """
    footprint = _box(18.0, 68.25, 1.12)          # ~250 km across

    # Short gap: contained even at its midpoint.
    short = ov.assess_pass(_gap(hours=2.0), _scene(footprint, at_hours=1.0))
    assert short["tier"] == "confirmed"

    # Long gap, pass near the start: still contained.
    edge = ov.assess_pass(_gap(hours=24.0), _scene(footprint, at_hours=1.2))
    assert edge["tier"] == "confirmed"

    # Long gap, pass at the midpoint: partial, and thin.
    middle = ov.assess_pass(_gap(hours=24.0), _scene(footprint, at_hours=12.0))
    assert middle["tier"] == "partial"
    assert middle["coverage_fraction"] < 0.25
    assert middle["reachable_area_km2"] > short["reachable_area_km2"]


# ---------------------------------------------------------------------------
# projection sanity
# ---------------------------------------------------------------------------

def test_local_projection_round_trips_within_a_metre():
    lat0, lon0 = 18.0, 68.0
    for lat, lon in ((18.5, 68.5), (17.2, 69.4), (18.0, 68.0)):
        x, y = ov._to_local(lat, lon, lat0, lon0)
        back_lat = lat0 + math.degrees(y / ov.EARTH_R_M)
        back_lon = lon0 + math.degrees(x / (ov.EARTH_R_M * math.cos(math.radians(lat0))))
        assert ov.haversine_m(lat, lon, back_lat, back_lon) < 1.0


def test_projected_distance_agrees_with_haversine():
    lat0, lon0 = 18.0, 68.0
    x, y = ov._to_local(18.0, 69.0, lat0, lon0)
    planar = math.hypot(x, y)
    great_circle = ov.haversine_m(lat0, lon0, 18.0, 69.0)
    assert abs(planar - great_circle) / great_circle < 0.01


def test_antimeridian_crossing_footprint_is_rejected_not_mangled():
    wkt = "POLYGON ((179 10, -179 10, -179 12, 179 12, 179 10))"
    assert ov.footprint_polygon(wkt, 11.0, 179.5) is None


def test_multipolygon_footprint_is_handled():
    wkt = (f"MULTIPOLYGON ((({_box(18.0, 68.0, 1.0)[10:-2]})), "
           f"(({_box(18.0, 70.0, 1.0)[10:-2]})))")
    poly = ov.footprint_polygon(wkt, 18.0, 69.0)
    assert poly is not None and poly.area > 0
