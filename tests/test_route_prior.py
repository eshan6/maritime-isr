"""Route-aware forward projection — the model, its guard rails, and the
contract it is not allowed to break.

ADR-032 refused to promote forward projection and named the fix it declined to
build: prediction has to be **route-aware**. :mod:`tracks.route_prior` is that
model. These tests are about the three things that can go wrong with it and
none of which a position-error number would catch.

**Is the flow field actually conditioned on heading?** The first version of
this model was not, and it was *worse than dead reckoning* — a cell holding a
waypoint holds both the inbound and the outbound course, and an unconditioned
field asked "which of these is hers" answers with the one nearest her current
heading, which is the inbound one, and so steers her straight through the
corner. The one place a route model has to earn its keep is the one place that
field could not. ``test_the_flow_field_answers_differently_by_heading`` is that
property, built on a two-way corridor where the right answers are opposite.

**Does it read the future?** A hull's own previous passages are the strongest
evidence available about her next hour, and using the passage she has not made
yet is reading the answer key. The causality guard is tested directly.

**Does it still honour the Phase 3 contract?** Association gating reads
``uncertainty_radius_m`` and the projection cone on the same physics cap. A
prediction change that widened a cone past what a hull could do would change
gating behaviour as a side effect, so the cap and the growth are tested on the
route-aware arm exactly as they already are on the dead-reckoned one.

Every track here is built from geodesy rather than loaded, so the tests state
the geometry they depend on instead of inheriting it from a corpus.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from maritime_isr.config import MAX_FEASIBLE_SPEED_KN
from maritime_isr.schemas.sources import AIS, RADAR
from maritime_isr.tracks import projection as proj
from maritime_isr.tracks import route_prior as rp


# ---------------------------------------------------------------------------
# fixtures: a corridor with a corner in it, run in both directions
# ---------------------------------------------------------------------------

class _Track:
    """The minimum surface `route_prior` and `projection` read from a track.

    A stand-in rather than a `BuiltTrack`, for the same reason
    `test_predictive` uses one: it proves these modules read position, speed
    and course over time and **nothing else** — no MMSI, no declared class, no
    sensor name. That is what lets a flow field fitted on AIS steer a radar
    contact.
    """

    def __init__(self, rows, track_id="t1", source=AIS, mmsi=None):
        self.points = pd.DataFrame(rows)
        self.points["quality"] = "ok"
        self.track_id = track_id
        self.track_key = track_id
        self.mmsi = mmsi
        self.source = source


T0 = 1_780_000_000.0
STEP_S = 120.0            # dense enough that a res-6 cell holds several fixes


def _walk_legs(lat, lon, legs, *, sog_kn=12.0, t0=T0, step_s=STEP_S):
    """Rows along a polyline given as (bearing, nautical miles) legs."""
    rows = []
    t = t0
    for bearing, nm in legs:
        n = max(1, int(round(nm * 1852.0 / (sog_kn * 0.514444 * step_s))))
        for _ in range(n):
            rows.append(dict(
                ts=pd.Timestamp(t, unit="s", tz="UTC"),
                lat=lat, lon=lon, sog_kn=sog_kn, cog_deg=bearing % 360.0,
                sigma_m=50.0))
            lat, lon = rp._advance(lat, lon, bearing,
                                   sog_kn * 0.514444 * step_s)
            t += step_s
    rows.append(dict(ts=pd.Timestamp(t, unit="s", tz="UTC"), lat=lat, lon=lon,
                     sog_kn=sog_kn, cog_deg=legs[-1][0] % 360.0, sigma_m=50.0))
    return rows


#: The corner. Eastbound traffic runs due east for 20 nm and then alters to
#: 045; westbound traffic runs the identical polyline in reverse. Every cell on
#: it therefore carries traffic on two opposite headings, which is exactly the
#: configuration an unconditioned flow field cannot represent.
CORNER_LAT, CORNER_LON = 15.0, 68.0
_EAST_LEGS = [(90.0, 20.0), (45.0, 20.0)]


def _eastbound(i: int):
    return _Track(_walk_legs(CORNER_LAT, CORNER_LON, _EAST_LEGS,
                             t0=T0 + i * 86_400.0),
                  track_id=f"E{i}", mmsi=100 + i)


def _westbound(i: int):
    """The same road, run the other way: start at the far end of the NE leg."""
    lat, lon = CORNER_LAT, CORNER_LON
    lat, lon = rp._advance(lat, lon, 90.0, 20.0 * 1852.0)
    lat, lon = rp._advance(lat, lon, 45.0, 20.0 * 1852.0)
    return _Track(_walk_legs(lat, lon, [(225.0, 20.0), (270.0, 20.0)],
                             t0=T0 + i * 86_400.0),
                  track_id=f"W{i}", mmsi=200 + i)


@pytest.fixture(scope="module")
def two_way_prior():
    tracks = [_eastbound(i) for i in range(6)] + [_westbound(i) for i in range(6)]
    return rp.fit_route_prior(tracks, hull_of=lambda t: str(t.mmsi)), tracks


# ---------------------------------------------------------------------------
# 1. the conditioning that makes it a route model
# ---------------------------------------------------------------------------

def test_the_flow_field_learns_the_corridor_at_all(two_way_prior):
    prior, _ = two_way_prior
    assert prior.n_cells > 5, "a 40 nm corridor should span several res-6 cells"
    assert prior.res == rp.FLOW_RES


def test_the_flow_field_answers_differently_by_heading(two_way_prior):
    """The property the whole model rests on.

    One cell, two hulls, opposite headings. If the field answers them the same
    it is a histogram of "courses seen here" and it will steer a northbound
    vessel south — which is not a subtle failure, it is the model being worse
    than the bonnet ornament it replaced.
    """
    prior, _ = two_way_prior
    # A point a third of the way along the eastbound leg, where both directions
    # have laid fixes.
    lat, lon = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 7.0 * 1852.0)

    east = prior.lookup(lat, lon, 90.0)
    west = prior.lookup(lat, lon, 270.0)
    assert east is not None and west is not None, (
        "both directions ran this cell often enough to have an opinion")

    assert abs(rp._signed_delta(90.0, east.course_deg)) < 45.0, (
        f"a vessel steering 090 here should be sent on eastwards, not "
        f"{east.course_deg:.0f}°")
    assert abs(rp._signed_delta(270.0, west.course_deg)) < 45.0, (
        f"a vessel steering 270 here should be sent on westwards, not "
        f"{west.course_deg:.0f}°")
    assert abs(rp._signed_delta(east.course_deg, west.course_deg)) > 90.0, (
        "the two answers must be opposite; if they agree, the field pooled "
        "both directions and is not conditioned on heading at all")


def test_an_unconditioned_field_cannot_do_that(two_way_prior, monkeypatch):
    """The counterfactual, so the conditioning is shown to be load-bearing.

    Pool every incoming course into one bin — which is the obvious way to build
    a flow field, and the way this one was built first — and the two-way
    corridor collapses to a mean of two opposite bearings. Whatever that mean
    is, it cannot be within 45° of both.
    """
    monkeypatch.setattr(rp, "_octant", lambda c: 0)
    _, tracks = two_way_prior
    pooled = rp.fit_route_prior(tracks, hull_of=lambda t: str(t.mmsi))
    pooled.octants = 1
    lat, lon = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 7.0 * 1852.0)
    e, w = pooled.lookup(lat, lon, 90.0), pooled.lookup(lat, lon, 270.0)
    assert e is not None and w is not None
    assert e.course_deg == pytest.approx(w.course_deg), (
        "an unconditioned field has one answer per cell by construction")
    agrees = sum(abs(rp._signed_delta(c, e.course_deg)) < 45.0
                 for c in (90.0, 270.0))
    assert agrees <= 1, (
        "one answer cannot serve both directions — this is the failure the "
        "heading conditioning exists to fix")


def test_the_field_carries_the_speed_traffic_actually_makes_good(two_way_prior):
    prior, _ = two_way_prior
    lat, lon = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 7.0 * 1852.0)
    m = prior.lookup(lat, lon, 90.0)
    assert m.sog_made_good_kn == pytest.approx(12.0, rel=0.15), (
        "speed made good is displacement over elapsed time, and these hulls "
        "ran a straight leg at 12 knots")


def test_a_cell_run_by_too_few_hulls_says_nothing():
    """One vessel's routing is not 'what traffic does here'.

    The same hazard as splitting an image dataset by chip rather than by scene:
    a single hull reporting densely clears an observation floor on her own.
    """
    prior = rp.fit_route_prior([_eastbound(0), _eastbound(1)],
                               hull_of=lambda t: str(t.mmsi))
    assert prior.n_modes == 0, (
        f"two hulls is under MIN_KEY_VESSELS={rp.MIN_KEY_VESSELS}, so no cell "
        f"should speak; got {prior.n_modes} modes")


def test_a_cell_nobody_has_run_returns_no_opinion(two_way_prior):
    """None and 0 are different answers — `baselines.is_unusual`'s rule."""
    prior, _ = two_way_prior
    assert prior.support(-40.0, 120.0) is None
    assert prior.lookup(-40.0, 120.0, 90.0) is None


def test_the_prior_lands_as_inspectable_rows_with_provenance(two_way_prior):
    prior, _ = two_way_prior
    rows = prior.as_rows()
    assert rows
    for key in ("h3_cell", "in_octant", "course_deg", "n_obs", "n_vessels",
                "source_id", "pipeline_version", "confidence", "is_synthetic"):
        assert key in rows[0], (
            "the flow field is a landed artifact like `baselines`, not a "
            "pickle — an operator is entitled to see why she was predicted "
            "round a corner")
    assert "synthetic" in prior.caveat().lower()


# ---------------------------------------------------------------------------
# 2. the hull's own history, and the clock it is read with
# ---------------------------------------------------------------------------

def test_own_history_only_returns_passages_that_already_ended():
    """Her transit of a cell she has not reached yet is the answer key."""
    tr = _eastbound(0)
    own = rp.fit_own_history([tr], hull_of=lambda t: str(t.mmsi))
    assert own.passages, "the walk should have produced passages"

    (hull, cell), passages = next(iter(own.passages.items()))
    p = passages[0]
    lat, lon = rp.tiling.cell_center(cell)

    before = own.lookup(hull, lat, lon, p.in_octant * 45.0 + 22.5,
                        before=p.t_end - 1.0)
    after = own.lookup(hull, lat, lon, p.in_octant * 45.0 + 22.5,
                       before=p.t_end + 1.0)
    assert before is None, (
        "a passage that had not finished yet must not inform a projection "
        "made before it — that is predicting the answer from the answer")
    assert after is not None


def test_own_history_separates_the_two_directions_through_one_cell():
    """A hull that runs a cell northbound on Monday and southbound on Friday
    has two passages going opposite ways; averaging them predicts her up the
    middle of the two, which is a course nobody steered."""
    east = _walk_legs(CORNER_LAT, CORNER_LON, [(90.0, 20.0)], t0=T0)
    west_start = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 20.0 * 1852.0)
    west = _walk_legs(west_start[0], west_start[1], [(270.0, 20.0)],
                      t0=T0 + 7 * 86_400.0)
    tr = _Track(east + west, track_id="RT", mmsi=999)
    own = rp.fit_own_history([tr], hull_of=lambda t: str(t.mmsi))
    both = [k for k in own.passages if len(own.passages[k]) >= 2]
    assert both, "the round trip should have re-entered at least one cell"
    for k in both:
        ts = [p.t_end for p in own.passages[k]]
        assert len(set(ts)) == len(ts), (
            "two visits to one cell are two passages with two end times; if "
            "they share one, the runs were merged and the causality cut in "
            "`lookup` has nothing to cut on")
    cell = both[0][1]
    lat, lon = rp.tiling.cell_center(cell)
    t_late = T0 + 30 * 86_400.0
    e = own.lookup("999", lat, lon, 90.0, before=t_late)
    w = own.lookup("999", lat, lon, 270.0, before=t_late)
    assert e is not None and w is not None
    assert abs(rp._signed_delta(e.course_deg, w.course_deg)) > 90.0


# ---------------------------------------------------------------------------
# 3. the walk
# ---------------------------------------------------------------------------

def _model(prior=None, own=None, calib=None):
    return rp.PredictionModel(prior=prior, own=own, calibration=calib or {})


def test_the_walk_follows_the_road_round_the_corner(two_way_prior):
    """The whole point, end to end: dead reckoning goes straight on at the
    waypoint and the route-aware walk turns."""
    prior, _ = two_way_prior
    m = _model(prior=prior)
    start = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 12.0 * 1852.0)
    sp = rp.step_along_prior(lat=start[0], lon=start[1], cog_deg=90.0,
                             sog_kn=12.0, lead_h=2.0, model=m)
    assert sp.support == "fleet_prior"
    assert sp.coverage >= rp.MIN_PRIOR_COVERAGE
    turn = rp._signed_delta(90.0, sp.final_course_deg)
    assert turn < -20.0 or turn > 20.0, (
        f"she should have been turned at the waypoint; ended on "
        f"{sp.final_course_deg:.0f}° having started on 090")
    # And she should be closer to the road than a straight run would be.
    dr = rp._advance(start[0], start[1], 90.0, 12.0 * 1852.0 * 2.0)
    truth = rp._advance(*rp._advance(start[0], start[1], 90.0, 8.0 * 1852.0),
                        45.0, 16.0 * 1852.0)
    end = sp.path[-1]
    assert rp._hav_m(*end, *truth) < rp._hav_m(*dr, *truth)


def test_the_walk_cannot_turn_faster_than_a_hull_turns(two_way_prior):
    """Without the rate limit the predictor snaps onto the outgoing bearing on
    entering a cell, cuts the corner on the inside, and puts her where no ship
    could have been."""
    prior, _ = two_way_prior
    m = _model(prior=prior)
    lead_h = 0.2
    sp = rp.step_along_prior(lat=CORNER_LAT, lon=CORNER_LON, cog_deg=90.0,
                             sog_kn=12.0, lead_h=lead_h, model=m,
                             turn_rate_max_deg_min=3.0)
    assert abs(rp._signed_delta(90.0, sp.final_course_deg)) \
        <= 3.0 * lead_h * 60.0 + 1e-6


def test_the_walk_says_not_checkable_where_there_is_no_road():
    """Falling back to dead reckoning is fine. Falling back *silently* is not:
    a consumer would trust a route-aware label more than it deserved."""
    sp = rp.step_along_prior(lat=-40.0, lon=120.0, cog_deg=90.0, sog_kn=12.0,
                             lead_h=3.0, model=_model(prior=rp.RoutePrior(
                                 res=rp.FLOW_RES)))
    assert sp.support == "not_checkable"
    assert sp.coverage == 0.0


def test_own_history_takes_precedence_over_the_fleet(two_way_prior):
    prior, tracks = two_way_prior
    own = rp.fit_own_history(tracks, hull_of=lambda t: str(t.mmsi))
    m = _model(prior=prior, own=own)
    start = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 7.0 * 1852.0)
    sp = rp.step_along_prior(lat=start[0], lon=start[1], cog_deg=90.0,
                             sog_kn=12.0, lead_h=1.0, model=m, hull="100",
                             made_at=T0 + 30 * 86_400.0)
    assert sp.support == "own_history", (
        "where the hull has run this leg before she is the better witness "
        "about her own next hour than the fleet is")


def test_truncate_path_ends_where_the_prediction_does(two_way_prior):
    """The curve an operator is shown has to end at the predicted position.
    Drawing the road on past it reads as 'and then she does this too'."""
    prior, _ = two_way_prior
    sp = rp.step_along_prior(lat=CORNER_LAT, lon=CORNER_LON, cog_deg=90.0,
                             sog_kn=12.0, lead_h=3.0, model=_model(prior=prior))
    for f in (0.25, 0.6, 1.0):
        path = rp.truncate_path(sp, f)
        assert path[0] == sp.path[0]
        assert path[-1] == pytest.approx(sp.at_fraction(f))


def test_at_fraction_is_linear_in_distance_not_in_step_index(two_way_prior):
    prior, _ = two_way_prior
    sp = rp.step_along_prior(lat=CORNER_LAT, lon=CORNER_LON, cog_deg=90.0,
                             sog_kn=12.0, lead_h=3.0, model=_model(prior=prior))
    half = sp.at_fraction(0.5)
    d = rp._hav_m(*sp.path[0], *half)
    assert d == pytest.approx(sp.length_m / 2.0, rel=0.02)


# ---------------------------------------------------------------------------
# 4. calibration
# ---------------------------------------------------------------------------

def test_motion_class_folds_for_sample_size_and_keeps_the_third_answer():
    assert rp.motion_class("Aframax") == "merchant"
    assert rp.motion_class("bulker") == "merchant"
    assert rp.motion_class("fishing") == "fishing"
    assert rp.motion_class("unclassified") == "unclassified"
    assert rp.motion_class(None) == "unclassified", (
        "'we could not say what she is' must never be given the commonest "
        "class's calibration — that asserts a type about every hull the "
        "classifier declined to name")


def test_the_calibration_is_measured_and_carries_its_own_error(two_way_prior):
    _, tracks = two_way_prior
    cal = rp.calibrate([("merchant", t) for t in tracks], model=None,
                       hull_of=lambda t: str(t.mmsi), leads=(0.5, 1.0),
                       max_samples=40)
    c = cal["merchant"]
    assert c.n_samples > 0
    assert c.fit_error_nm, (
        "a cone is reported with the error it was sized against, or a reader "
        "cannot tell a tight model from a tight percentile")
    assert c.as_dict()["cone_percentile"] == rp.CONE_PERCENTILE


def test_calibration_for_falls_back_through_the_fold_then_to_unclassified():
    cal = {"merchant": rp.TypeCalibration("merchant",
                                          advance_factor={1.0: 0.8}),
           "unclassified": rp.TypeCalibration("unclassified")}
    m = rp.PredictionModel(calibration=cal, fallback=cal["unclassified"])
    assert m.calibration_for("merchant").vessel_type == "merchant"
    assert m.calibration_for("Suezmax").vessel_type == "merchant", (
        "a fine type with no calibration of its own folds to its motion class")
    assert m.calibration_for(None).vessel_type == "unclassified"


# ---------------------------------------------------------------------------
# 5. the Phase 3 contract, on the route-aware arm
# ---------------------------------------------------------------------------

def test_a_route_aware_cone_still_grows_with_lead_time(two_way_prior):
    prior, _ = two_way_prior
    m = _model(prior=prior)
    near = proj.project_route_aware(lat=CORNER_LAT, lon=CORNER_LON, sog_kn=12.0,
                                    cog_deg=90.0, made_at=0.0,
                                    valid_for=3600.0, model=m)
    far = proj.project_route_aware(lat=CORNER_LAT, lon=CORNER_LON, sog_kn=12.0,
                                   cog_deg=90.0, made_at=0.0,
                                   valid_for=6 * 3600.0, model=m)
    assert far.radius_m > near.radius_m
    assert far.confidence < near.confidence


def test_a_route_aware_cone_is_still_capped_by_physics(two_way_prior):
    """Phase 3 association gating reads this cap. A prediction change that
    widened it would change gating as a side effect."""
    prior, _ = two_way_prior
    m = _model(prior=prior)
    m.calibration["merchant"] = rp.TypeCalibration(
        "merchant", cone_growth_m_per_hour=10_000_000.0)
    p = proj.project_route_aware(lat=CORNER_LAT, lon=CORNER_LON, sog_kn=12.0,
                                 cog_deg=90.0, made_at=0.0, valid_for=600.0,
                                 model=m, vessel_type="merchant",
                                 position_sigma_m=10_000_000.0)
    cap = MAX_FEASIBLE_SPEED_KN * 1852.0 * (600.0 / 3600.0)
    assert p.radius_m <= cap + 1.0
    assert rp.physics_cap_m(600.0 / 3600.0) == pytest.approx(cap)


def test_route_support_is_three_valued_and_says_which(two_way_prior):
    prior, tracks = two_way_prior
    own = rp.fit_own_history(tracks, hull_of=lambda t: str(t.mmsi))
    start = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 7.0 * 1852.0)
    common = dict(sog_kn=12.0, cog_deg=90.0, made_at=T0 + 30 * 86_400.0,
                  valid_for=T0 + 30 * 86_400.0 + 3600.0)

    fleet = proj.project_route_aware(lat=start[0], lon=start[1],
                                     model=_model(prior=prior), **common)
    hers = proj.project_route_aware(lat=start[0], lon=start[1],
                                    model=_model(prior=prior, own=own),
                                    hull="100", **common)
    nowhere = proj.project_route_aware(lat=-40.0, lon=120.0,
                                       model=_model(prior=prior), **common)

    assert fleet.route_support == "fleet_prior"
    assert hers.route_support == "own_history"
    assert nowhere.route_support == "not_checkable"
    assert {fleet.route_support, hers.route_support, nowhere.route_support} == {
        "fleet_prior", "own_history", "not_checkable"}
    assert "dead reckoning" in nowhere.basis, (
        "a projection that fell back must say so in its basis, not present "
        "itself as route-aware")


def test_a_not_checkable_projection_really_is_dead_reckoning(two_way_prior):
    """`not_checkable` means dead reckoning **all the way down**, not a path
    that was bent by the two cells that happened to have an opinion.

    A hull crossing a corridor clips a couple of its cells; below
    ``MIN_PRIOR_COVERAGE`` those cells have no business swinging her onto a
    road she is not on. Measured on the synthetic corpus, keeping the
    part-bent path put the route arm *behind* dead reckoning off-lane, which is
    a predictor doing harm in the water it does not know — the same shape of
    error as tightening a cone over an unwatched cell.
    """
    prior, _ = two_way_prior
    m = _model(prior=prior)
    # A start well outside the corridor: nothing on the walk has support.
    p = proj.project_route_aware(lat=-40.0, lon=120.0, sog_kn=12.0,
                                 cog_deg=90.0, made_at=0.0,
                                 valid_for=3 * 3600.0, model=m)
    assert p.route_support == "not_checkable"
    dr = proj.project_from(lat=-40.0, lon=120.0, sog_kn=12.0, cog_deg=90.0,
                           made_at=0.0, valid_for=3 * 3600.0)
    assert (p.lat, p.lon) == pytest.approx((dr.lat, dr.lon)), (
        "with no calibrated advance factor the fallback must land exactly "
        "where dead reckoning lands")
    assert p.path == [], (
        "and it must not hand the UI a curve it did not follow")


def test_a_route_aware_projection_carries_the_curve_it_is_asking_you_to_believe(
        two_way_prior):
    prior, _ = two_way_prior
    p = proj.project_route_aware(lat=CORNER_LAT, lon=CORNER_LON, sog_kn=12.0,
                                 cog_deg=90.0, made_at=0.0,
                                 valid_for=3 * 3600.0, model=_model(prior=prior))
    assert len(p.path) > 2
    assert p.path[-1] == pytest.approx((p.lat, p.lon))
    row = p.as_dict()
    for key in ("route_support", "prior_coverage", "cone_modulation", "path",
                "basis", "pipeline_version"):
        assert key in row


def test_dead_reckoning_is_unchanged_when_no_model_is_passed():
    """Phase 3 and the imaging-opportunity layer already call `project`. A
    route prior must not change their answers under a refactor."""
    rows = _walk_legs(CORNER_LAT, CORNER_LON, [(90.0, 30.0)])
    tr = _Track(rows, mmsi=1)
    at = float(tr.points["ts"].iloc[40].timestamp())
    made = float(tr.points["ts"].iloc[10].timestamp())
    got = proj.project(tr, at=at, made_from=made)
    row = tr.points.iloc[10]
    want = proj.project_from(lat=float(row["lat"]), lon=float(row["lon"]),
                             sog_kn=float(row["sog_kn"]),
                             cog_deg=float(row["cog_deg"]),
                             made_at=made, valid_for=at,
                             position_sigma_m=float(row["sigma_m"]),
                             track_id=tr.track_id, track_source=AIS.name)
    assert (got.lat, got.lon, got.radius_m) == \
        pytest.approx((want.lat, want.lon, want.radius_m))
    assert got.route_support == "not_checkable"


# ---------------------------------------------------------------------------
# 6. the cone modulation, and the three-valued honesty in it
# ---------------------------------------------------------------------------

def test_an_absent_baseline_never_tightens_the_cone():
    """A cone quietly narrowed over an unmonitored patch of ocean manufactures
    a departure out of ignorance. That is the coverage-versus-silence
    confusion this project keeps finding in new clothes."""
    f, why = proj._cone_modulation(None, None, 15.0, 68.0, 12.0)
    assert f == pytest.approx(1.0)
    assert "not checkable" in why and "no usable area baseline" in why


class _NoOpinionBaselines:
    """A baseline index that has watched this water too little to speak —
    `baselines.is_unusual`'s third value, in object form."""

    class _B:
        usable = False
        n_observations = 3

        def percentile(self, *_a):
            return None

    def at(self, lat, lon):
        return self._B()


def test_a_baseline_with_no_opinion_leaves_the_cone_alone():
    f, why = proj._cone_modulation(20.0, _NoOpinionBaselines(), 15.0, 68.0, 12.0)
    assert f == pytest.approx(1.0, abs=0.01)
    assert "no usable area baseline" in why


def test_a_tighter_lane_earns_a_tighter_cone_and_a_wider_one_a_wider():
    tight, _ = proj._cone_modulation(5.0, None, 15.0, 68.0, 12.0)
    loose, _ = proj._cone_modulation(60.0, None, 15.0, 68.0, 12.0)
    assert tight < 1.0 < loose
    assert proj.MODULATION_MIN <= tight and loose <= proj.MODULATION_MAX


def test_the_modulation_is_bounded_both_ways():
    """A modulation that ran to zero would give a cone nothing can be inside;
    one that ran unbounded would give a cone nothing can be outside. Both are
    ways of declining to make a claim."""
    for spread in (0.0, 1e6):
        f, _ = proj._cone_modulation(spread, None, 15.0, 68.0, 12.0)
        assert proj.MODULATION_MIN <= f <= proj.MODULATION_MAX


# ---------------------------------------------------------------------------
# 7. source-blindness — Area 3's requirement, one module along
# ---------------------------------------------------------------------------

def test_the_predictor_gives_radar_and_ais_the_same_answer(two_way_prior):
    """A flow field fitted on AIS tracks must steer a radar contact
    identically. If it did not, that would be a defect in the fusion core —
    the brief's words, and ADR-032 (c)'s test one module along."""
    prior, _ = two_way_prior
    rows = _walk_legs(CORNER_LAT, CORNER_LON, [(90.0, 25.0)])
    ais = _Track(rows, track_id="A", source=AIS, mmsi=7)
    radar = _Track(list(rows), track_id="A", source=RADAR, mmsi=None)
    m = _model(prior=prior)
    at = float(ais.points["ts"].iloc[60].timestamp())
    made = float(ais.points["ts"].iloc[10].timestamp())
    pa = proj.project(ais, at=at, made_from=made, model=m, hull="")
    pr = proj.project(radar, at=at, made_from=made, model=m, hull="")
    assert (pa.lat, pa.lon, pa.radius_m) == \
        pytest.approx((pr.lat, pr.lon, pr.radius_m))
    assert pa.route_support == pr.route_support
