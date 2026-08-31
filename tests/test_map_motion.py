"""The moving map: reporting sessions, and forward projection as a served layer.

Two changes to what the map is allowed to draw, and one of them is a
correctness fix rather than a feature.

**1. A track is segmented into reporting sessions before it is animated.**
The map interpolated between whichever two fixes bracketed the clock, anywhere
in a vessel's series. Across a four-minute report interval that is a fair
reading; across the 206 inter-fix gaps in the landed corpus that run over six
hours — the longest is five days — it draws a vessel steaming steadily along a
line nobody observed. `/tracks` now returns the session boundaries, measured on
the FULL series before decimation, because decimation is exactly what destroys
the evidence of a silence: at 200 points over a fifty-day track, consecutive
samples are hours apart whether or not anything was heard in between.

**2. Forward projection is served, not reimplemented.** The map draws where
dead reckoning says each broadcasting vessel is going. The model behind that
line is `tracks/projection.py`, whose every constant carries a measurement; a
JavaScript copy of it would be a second, uncalibrated predictor free to drift
from the first. So the browser asks the API and draws the answer.

The client halves — the dashed stroke, the fading trail, the cone drawn only on
the selected hull — are MapLibre paint properties and are verified in a browser.
What is reachable from pytest is the data those halves consume, which is what
this module holds.
"""
from __future__ import annotations

import pytest

from maritime_isr.api import service
from maritime_isr.config import AIS_SESSION_BREAK_HOURS, MAX_FEASIBLE_SPEED_KN
from maritime_isr.tracks.projection import CONE_GROWTH_M_PER_HOUR, MAX_LEAD_HOURS

BREAK_S = AIS_SESSION_BREAK_HOURS * 3600.0


# ---------------------------------------------------------------------------
# session segmentation — the pure part
# ---------------------------------------------------------------------------

def test_an_unbroken_series_is_one_session():
    """Nothing to report on a vessel that never stopped transmitting. Index 0 is
    never returned: every track opens a session by definition and saying so
    would make the field noise the client has to filter."""
    assert service._session_starts([0, 240, 480, 720], BREAK_S) == []


def test_a_silence_longer_than_the_threshold_starts_a_new_session():
    epochs = [0, 240, 480, 480 + int(BREAK_S) + 1, 480 + int(BREAK_S) + 241]
    assert service._session_starts(epochs, BREAK_S) == [3]


def test_a_silence_exactly_at_the_threshold_does_not_break():
    """The comparison is strictly greater, and it matters which way it falls: a
    threshold that fires ON the boundary would split a vessel reporting on a
    clean six-hourly schedule into a session per fix, and she would never be
    drawn at all."""
    epochs = [0, int(BREAK_S), 2 * int(BREAK_S)]
    assert service._session_starts(epochs, BREAK_S) == []


def test_every_gap_is_reported_not_just_the_first():
    """A hull that goes quiet repeatedly — which is what a fishing vessel
    working out of receiver range does — has to come back segmented at every
    silence. Returning only the first would join all the rest."""
    g = int(BREAK_S) + 1
    epochs = [0, 100, 100 + g, 200 + g, 200 + 2 * g]
    assert service._session_starts(epochs, BREAK_S) == [2, 4]


def test_the_session_threshold_is_not_the_identity_threshold():
    """`TRACK_BREAK_DAYS` is seven days and answers "is this still the same
    hull". This is six hours and answers "may we draw a line between these two
    fixes". Collapsing them would either fabricate a week of steaming or shred
    every track into fragments."""
    from maritime_isr.config import TRACK_BREAK_DAYS
    assert AIS_SESSION_BREAK_HOURS < TRACK_BREAK_DAYS * 24


# ---------------------------------------------------------------------------
# session boundaries as /tracks serves them
# ---------------------------------------------------------------------------

def _tracks():
    r = service.list_tracks(max_vessels=250, max_points=200)
    if not r["items"]:
        pytest.skip("no AIS positions landed")
    return r


def test_every_track_carries_its_breaks():
    """Present on every item, empty list included. A missing field and an empty
    one are indistinguishable to `tr.breaks || []` on the client, which is
    precisely how a track with real gaps would silently be drawn as continuous."""
    for it in _tracks()["items"]:
        assert isinstance(it.get("breaks"), list)
        assert all(isinstance(b, int) for b in it["breaks"])


def test_breaks_index_into_points_and_are_ordered():
    for it in _tracks()["items"]:
        n = len(it["points"])
        assert all(0 < b < n for b in it["breaks"]), it["vessel_id"]
        assert it["breaks"] == sorted(set(it["breaks"])), it["vessel_id"]


def test_a_break_is_where_the_silence_actually_is():
    """The point after a break must be more than the threshold later than the
    point before it. This is the assertion that the boundaries survived
    decimation: a stride that skipped a session's first or last fix would move
    the boundary and leave a break sitting in the middle of continuous
    reporting."""
    for it in _tracks()["items"]:
        pts = it["points"]
        for b in it["breaks"]:
            assert pts[b][2] - pts[b - 1][2] > BREAK_S, (
                f"{it['vessel_id']}: break at {b} spans only "
                f"{pts[b][2] - pts[b - 1][2]}s")


def test_no_real_silence_in_the_landed_data_goes_unmarked():
    """The other half, and the one that catches the real failure mode: a
    silence in the LANDED table with no break marking it in what was served.
    That is the pair the client would interpolate across.

    Checked against `ais_position` rather than against the served points, and
    the difference is the whole point of the test. Two kept points far apart in
    time are not evidence of a silence — decimation puts them there. This
    vessel reports 13,881 times with no raw gap over 58 minutes, and at a
    200-point budget her samples still land up to sixteen hours apart; asserting
    on the served spacing calls that a missed break when the data says she never
    stopped talking. Only the raw series knows.
    """
    from maritime_isr.api.reader import open_reader
    items = {it["vessel_id"]: it for it in _tracks()["items"]}
    with open_reader() as reader:
        gaps = reader.rows(
            "WITH d AS (SELECT vessel_id, epoch(ts) AS t, "
            "  lag(epoch(ts)) OVER (PARTITION BY vessel_id ORDER BY ts) AS prev "
            "  FROM ais_position WHERE lat IS NOT NULL AND lon IS NOT NULL) "
            "SELECT vessel_id, prev, t FROM d WHERE t - prev > ?", [BREAK_S])
    if not gaps:
        pytest.skip("no silence in this corpus is longer than the threshold")
    checked = 0
    for g in gaps:
        it = items.get(service.canonical_id(g["vessel_id"]))
        if it is None:
            continue          # dropped by the vessel cap, not by the segmenter
        pts, breaks = it["points"], it["breaks"]
        # A session's first and last fix are always kept, so a real silence has
        # to appear as a break whose two sides are exactly its endpoints.
        assert any(pts[b - 1][2] == int(g["prev"]) and pts[b][2] == int(g["t"])
                   for b in breaks), (
            f"{it['vessel_id']}: {int(g['t'] - g['prev'])}s silence at "
            f"{int(g['prev'])} reached the client unmarked")
        checked += 1
    assert checked, "no landed silence belonged to a served vessel"


def test_points_stay_in_time_order_across_the_whole_track():
    """Decimating per session must not reorder anything: the client binary
    searches these."""
    for it in _tracks()["items"]:
        ts = [p[2] for p in it["points"]]
        assert ts == sorted(ts), it["vessel_id"]


def test_segmentation_did_not_cost_the_truncation_report():
    """The cap semantics `test_map_legibility` pinned still hold — decimating
    inside sessions changes how many points come back, never how many vessels."""
    r = _tracks()
    assert "matched_vessels" in r and "truncated" in r


# ---------------------------------------------------------------------------
# forward projection as a served layer
# ---------------------------------------------------------------------------

def _busy_moment() -> float:
    """An instant with traffic in it. Taken from the middle of the longest
    track rather than from the window edges, where by construction almost
    nobody is broadcasting."""
    items = _tracks()["items"]
    longest = max(items, key=lambda it: len(it["points"]))
    return float(longest["points"][len(longest["points"]) // 2][2])


def test_projections_come_back_for_the_broadcasting_fleet():
    at = _busy_moment()
    r = service.project_active(at=at, lead_hours=3.0)
    assert r["items"], "nobody was projected at an instant with traffic in it"
    assert r["active_vessels"] >= len(r["items"])


def test_a_projection_is_made_from_a_fix_at_or_before_the_clock():
    """**Nothing here reads ahead.** A projection made from a fix later than the
    clock would be using the answer to predict itself, and the drawn line would
    be a replay dressed as a forecast."""
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=3.0)["items"]:
        assert p["made_at"] <= at, p["vessel_id"]


def test_the_origin_fix_is_within_the_continuity_window():
    """Active means heard recently, on the same threshold sessions are cut on,
    so the set projected and the set drawn moving are the same set."""
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=3.0)["items"]:
        assert 0 <= p["stale_minutes"] <= AIS_SESSION_BREAK_HOURS * 60 + 1


def test_the_path_starts_at_the_clock_and_reaches_the_requested_lead():
    at = _busy_moment()
    r = service.project_active(at=at, lead_hours=3.0)
    for p in r["items"]:
        assert p["path"][0][2] == int(at)
        assert p["path"][-1][2] == pytest.approx(at + 3 * 3600, abs=2)


def test_the_cone_grows_along_the_path_and_never_shrinks():
    """The growth IS the honesty. A cone that did not widen with lead time
    would assert a two-hour-old prediction as confidently as a fresh one."""
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=6.0)["items"]:
        radii = [q[3] for q in p["path"]]
        assert radii == sorted(radii), p["vessel_id"]
        assert radii[-1] > radii[0]


def test_confidence_falls_as_the_cone_widens():
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=6.0)["items"]:
        conf = [q[4] for q in p["path"]]
        assert conf == sorted(conf, reverse=True), p["vessel_id"]
        assert all(0.0 <= c <= 1.0 for c in conf)


def test_a_stale_fix_produces_a_wide_cone_at_the_very_first_point():
    """The staleness is carried by the cone rather than hidden. A vessel last
    heard four hours ago is not "here"; she is somewhere in a circle, and the
    circle has to be that size at `path[0]` or the map understates what it
    knows at the exact moment it matters most."""
    at = _busy_moment()
    items = service.project_active(at=at, lead_hours=3.0)["items"]
    stale = [p for p in items if p["stale_minutes"] > 60]
    if not stale:
        pytest.skip("no vessel in this corpus was an hour stale at that instant")
    for p in stale:
        lead_h = p["stale_minutes"] / 60.0
        assert p["path"][0][3] >= min(CONE_GROWTH_M_PER_HOUR * lead_h,
                                      MAX_FEASIBLE_SPEED_KN * 1852.0 * lead_h) * 0.99


def test_the_cone_never_exceeds_what_a_hull_could_physically_do():
    """The physics cap, checked on the served numbers rather than trusted from
    the module. Without it a long lead produces a cone larger than the ocean and
    the layer says nothing while looking like it says something."""
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=12.0)["items"]:
        for q in p["path"]:
            lead_h = (q[2] - p["made_at"]) / 3600.0
            assert q[3] <= MAX_FEASIBLE_SPEED_KN * 1852.0 * lead_h + 1.0


def test_the_path_is_truncated_at_the_projection_ceiling_not_extrapolated():
    """`MAX_LEAD_HOURS` is where the module refuses, and staleness plus lead can
    reach it. The drawn path stops there rather than carrying on past a
    boundary the model itself declines to cross."""
    at = _busy_moment()
    for p in service.project_active(at=at, lead_hours=12.0)["items"]:
        for q in p["path"]:
            assert (q[2] - p["made_at"]) / 3600.0 <= MAX_LEAD_HOURS + 1e-6


def test_a_moment_with_nobody_broadcasting_says_so_rather_than_returning_bare_empty():
    """An empty projection layer with no sentence attached reads as "no vessel
    here is going anywhere", which is a claim we did not make. Same rule the
    truncation notes follow."""
    r = service.project_active(at=0.0, lead_hours=3.0)
    assert r["items"] == []
    assert r["note"], "an empty answer arrived with nothing explaining it"


def test_every_response_states_the_model_and_its_limit():
    """The caveat is load-bearing, not decoration. `projection.py` measured that
    departure from a dead-reckoned track flags 98% of the fleet — every vessel
    alters at every waypoint — which is why this system does not carry it as a
    suspicion factor. A predicted track drawn without that sentence invites the
    operator to read a routine course change as a finding."""
    r = service.project_active(at=_busy_moment(), lead_hours=3.0)
    assert "dead reckoning" in r["basis"].lower()
    assert "not a route model" in r["caveat"].lower()
    assert "not a finding" in r["caveat"].lower()
    assert r["cone_growth_nm_per_hour"] > 0


def test_truncation_keeps_the_freshest_fixes_and_reports_itself():
    at = _busy_moment()
    full = service.project_active(at=at, lead_hours=3.0)
    if full["active_vessels"] < 2:
        pytest.skip("fewer than two vessels broadcasting at that instant")
    r = service.project_active(at=at, lead_hours=3.0, max_vessels=1)
    assert r["truncated"] is True
    assert r["note"] and "truncated" in r["note"].lower()
    assert len(r["items"]) <= 1
    # the one kept is the most recently heard, not an arbitrary slice
    assert r["items"][0]["stale_minutes"] == min(
        p["stale_minutes"] for p in full["items"])
