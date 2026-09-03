"""Motion novelty — the four properties that make it a different signal from
the departure residual, and the two that keep it honest.

ADR-042 measured "did she depart from her own dead-reckoned path" at chance and
named the mechanism: **a waypoint turn is a large residual whether or not the
corner is odd**, so the residual does not select. :mod:`tracks.novelty` asks the
flow field a different question — *given that traffic turns here, is what she
did surprising?* — and the tests that matter are the ones that show the two
signals disagree in the way the mechanism predicts.

So the first two tests are a matched pair on one corridor with one corner:

* a hull that **takes** the corner the way the fleet takes it has a large
  residual and a **small** novelty, and
* a hull that **runs straight through** it has a comparable residual and a
  **large** novelty.

If those two ever converge, this module has become the residual again wearing a
different name, and the precision measured on it is the old number.

The remaining tests are the guard rails: that water nobody watches is never
called "off the customary route" (the same false-positive-by-construction
CLAUDE.md forbids for offshore AIS gaps), that a tight lane cannot manufacture
surprise out of a floor-less denominator, that "we could not check" stays
distinct from "she was fine", and that the split which every reported number
depends on actually refuses a hull on two sides of it.

Every track here is built from geodesy rather than loaded, so each test states
the geometry it depends on instead of inheriting it from a corpus.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from maritime_isr.schemas.sources import AIS, RADAR
from maritime_isr.tracks import novelty as nv
from maritime_isr.tracks import prediction_eval as pe
from maritime_isr.tracks import route_prior as rp


# ---------------------------------------------------------------------------
# fixtures: one corridor, with a corner in it
# ---------------------------------------------------------------------------

class _Track:
    """The minimum surface `novelty` reads from a track.

    A stand-in rather than a `BuiltTrack`, and deliberately so: it carries
    position, speed and course over time and nothing else — no declared class,
    no sensor name — which is what proves a field fitted on AIS can score a
    radar contact (`test_novelty_is_source_blind`).
    """

    def __init__(self, rows, track_id="t1", source=AIS, mmsi=None):
        self.points = pd.DataFrame(rows)
        self.points["quality"] = "ok"
        self.track_id = track_id
        self.track_key = track_id
        self.mmsi = mmsi
        self.source = source


T0 = 1_780_000_000.0
STEP_S = 120.0

CORNER_LAT, CORNER_LON = 15.0, 68.0
#: Due east for 20 nm, then alter to 045 for 20 nm. Every hull in the fitting
#: fleet runs exactly this, so "what traffic does here" includes the turn.
LEGS_WITH_CORNER = [(90.0, 20.0), (45.0, 20.0)]
#: The same start, the same speed, and no turn at all.
LEGS_STRAIGHT_THROUGH = [(90.0, 40.0)]


def _walk_legs(lat, lon, legs, *, sog_kn=12.0, t0=T0, step_s=STEP_S):
    rows = []
    t = t0
    for bearing, nm in legs:
        n = max(1, int(round(nm * 1852.0 / (sog_kn * 0.514444 * step_s))))
        for _ in range(n):
            rows.append(dict(ts=pd.Timestamp(t, unit="s", tz="UTC"),
                             lat=lat, lon=lon, sog_kn=sog_kn,
                             cog_deg=bearing % 360.0, sigma_m=50.0))
            lat, lon = rp._advance(lat, lon, bearing,
                                   sog_kn * 0.514444 * step_s)
            t += step_s
    rows.append(dict(ts=pd.Timestamp(t, unit="s", tz="UTC"), lat=lat, lon=lon,
                     sog_kn=sog_kn, cog_deg=legs[-1][0] % 360.0, sigma_m=50.0))
    return rows


def _fleet(n=8, legs=LEGS_WITH_CORNER, sog_kn=12.0, tag="F"):
    return [_Track(_walk_legs(CORNER_LAT, CORNER_LON, legs, sog_kn=sog_kn,
                              t0=T0 + i * 86_400.0),
                   track_id=f"{tag}{i}", mmsi=1000 + i)
            for i in range(n)]


@pytest.fixture(scope="module")
def corner_field():
    """A field fitted on eight hulls that all take the same corner."""
    fleet = _fleet()
    return nv.fit_traffic_field(fleet, hull_of=lambda t: str(t.mmsi))


def _mean_sigma(track, field):
    sigs = [f.course_sigma for f in nv.fix_surprises(track, field)
            if f.course_sigma is not None]
    assert sigs, "the field had no answer anywhere on this track"
    return sum(sigs) / len(sigs), max(sigs)


# ---------------------------------------------------------------------------
# 1. the matched pair — the reason this module exists
# ---------------------------------------------------------------------------

def test_a_hull_that_takes_the_corner_is_not_surprising(corner_field):
    """The half of the pair the residual gets wrong.

    She alters 45° at the waypoint, so a dead-reckoned projection made before
    the corner puts her miles from where she ends up — a large residual, and
    ADR-042's measurement says that residual fires on nearly every hull in the
    fleet. The flow field knows traffic turns here, so her novelty is small.
    """
    her = _Track(_walk_legs(CORNER_LAT, CORNER_LON, LEGS_WITH_CORNER,
                            t0=T0 + 500_000.0),
                 track_id="held-out", mmsi=9001)
    mean, peak = _mean_sigma(her, corner_field)
    assert mean < 1.5, (
        f"a hull doing exactly what the fitted fleet does here scored "
        f"{mean:.2f} sigma on average; if taking the customary corner is "
        f"surprising, this signal is the residual again")


def test_a_hull_that_runs_straight_through_the_corner_is_surprising(corner_field):
    """The other half. Same start, same speed, no turn — and the field notices.

    This is the discrimination the residual could not make: both hulls depart
    from *something*, and only one of them departs from what traffic does.
    """
    her = _Track(_walk_legs(CORNER_LAT, CORNER_LON, LEGS_STRAIGHT_THROUGH,
                            t0=T0 + 500_000.0),
                 track_id="odd-one", mmsi=9002)
    _, peak = _mean_sigma(her, corner_field)

    conformer = _Track(_walk_legs(CORNER_LAT, CORNER_LON, LEGS_WITH_CORNER,
                                  t0=T0 + 500_000.0),
                       track_id="held-out", mmsi=9001)
    _, conformer_peak = _mean_sigma(conformer, corner_field)

    assert peak > conformer_peak * 2.0, (
        f"the hull that ignored the corner peaked at {peak:.2f} sigma against "
        f"{conformer_peak:.2f} for the one that took it; if those are "
        f"comparable the field is not separating conformers from novelties")


# ---------------------------------------------------------------------------
# 2. the coverage rule
# ---------------------------------------------------------------------------

def test_water_nobody_watches_is_never_off_the_road(corner_field):
    """The false positive this system forbids by construction.

    A vessel in an area where we have established no customary route is not off
    it — we simply have not watched there. Calling that "off the customary
    route" is the same error as calling an out-of-coverage AIS gap dark
    (CLAUDE.md §6), and it is the error that would otherwise flag every hull
    that leaves the corpus's one corridor.
    """
    far_lat, far_lon = CORNER_LAT + 6.0, CORNER_LON + 6.0     # ~700 km away
    assert corner_field.road_status(far_lat, far_lon, 90.0) == nv.UNWATCHED


def test_off_the_road_needs_a_watched_neighbourhood(corner_field):
    """…and inside the watched neighbourhood, an unsupported cell IS off-road.

    Otherwise the coverage rule would swallow the signal along with the false
    positive: a hull that steps sideways out of a busy corridor is exactly the
    case this is for.
    """
    # A few km abeam the corridor: close enough that the ring-2 neighbourhood
    # is full of fitted traffic, far enough that her own cell is not on it.
    lat, lon = rp._advance(CORNER_LAT, CORNER_LON, 90.0, 10.0 * 1852.0)
    beside = rp._advance(lat, lon, 0.0, 12_000.0)
    status = corner_field.road_status(beside[0], beside[1], 90.0)
    assert status in (nv.OFF_ROAD, nv.ON_ROAD)
    if status == nv.OFF_ROAD:
        cell = corner_field.prior.cell_of(beside[0], beside[1])
        assert corner_field._neighbourhood_obs(cell) >= \
            corner_field.min_neighbourhood_obs


def test_unwatched_fixes_never_count_against_a_hull(corner_field):
    """`off_road_fraction` excludes them from the denominator rather than
    counting them as on-road. They are neither, and a boolean would have to
    pick one."""
    p = nv.HullMotionProfile(hull="h", n_scored=100, n_checkable=10,
                             n_off_road=10, n_unwatched=80)
    assert p.off_road_fraction == pytest.approx(0.5)
    assert p.check_coverage == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 3. the denominators
# ---------------------------------------------------------------------------

def test_a_tight_lane_cannot_manufacture_sigma(corner_field):
    """The spread floor, and why it is not cosmetic.

    A synthetic corridor is fitted from perfectly-repeated tracks, so its
    circular spread can come out at a fraction of a degree. Without a floor,
    a hull half a degree off — which is inside AIS's own quantisation — would
    score hundreds of sigma and every operating point would be noise.
    """
    tight = [m for per in corner_field.prior.cells.values()
             for m in per.values() if m.spread_deg < nv.MIN_SPREAD_DEG]
    if not tight:
        pytest.skip("this fitted field has no lane tighter than the floor")
    her = _Track(_walk_legs(CORNER_LAT, CORNER_LON, LEGS_WITH_CORNER,
                            t0=T0 + 500_000.0), track_id="c", mmsi=9003)
    _, peak = _mean_sigma(her, corner_field)
    assert peak < 50.0, (
        f"a conforming hull peaked at {peak:.0f} sigma — the spread floor is "
        f"not being applied and a tight lane is manufacturing surprise")


def test_not_checkable_is_none_and_never_zero(corner_field):
    """Zero sigma means "exactly what the lane does". A fix the field could not
    speak for must not claim that."""
    far = _Track(_walk_legs(CORNER_LAT + 6.0, CORNER_LON + 6.0,
                            [(90.0, 10.0)]), track_id="far", mmsi=9004)
    fixes = nv.fix_surprises(far, corner_field)
    assert fixes, "the track produced no scoreable fixes at all"
    assert all(f.course_sigma is None for f in fixes)
    assert all(not f.checkable for f in fixes)
    assert all(f.road == nv.UNWATCHED for f in fixes)


# ---------------------------------------------------------------------------
# 4. source blindness — the ADR-032/033 rule
# ---------------------------------------------------------------------------

def test_novelty_is_source_blind(corner_field):
    """A radar track and an AIS track with identical motion score identically.

    This is what keeps the signal usable in `fusion/` without a source-specific
    branch (CLAUDE.md §4.5): if a radar contact scored differently, the score
    would be reading the sensor rather than the sea.
    """
    rows = _walk_legs(CORNER_LAT, CORNER_LON, LEGS_STRAIGHT_THROUGH,
                      t0=T0 + 700_000.0)
    a = _Track(rows, track_id="ais", source=AIS, mmsi=9005)
    b = _Track(rows, track_id="rdr", source=RADAR, mmsi=None)
    assert _mean_sigma(a, corner_field) == _mean_sigma(b, corner_field)


def test_novelty_and_its_harness_never_read_the_answer_key():
    """`tests/test_scenario.py` already runs this check across every detection
    path, and both of this session's modules live in one of them. It is
    restated here because it is the property the whole measurement is worth:
    the answer key would make these numbers look wonderful and mean nothing.

    The same AST check is reused rather than a fresh grep, so a docstring that
    *names* the rule is not mistaken for a module that *reads* it.
    """
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_scenario import _truth_references
    repo = Path(__file__).resolve().parent.parent
    for mod in ("tracks/novelty.py", "tracks/prediction_eval.py"):
        hits = _truth_references(repo / "maritime_isr" / mod)
        assert not hits, f"{mod} reads ground truth: {hits}"


# ---------------------------------------------------------------------------
# 5. the split and the scoring the reported numbers depend on
# ---------------------------------------------------------------------------

def test_the_three_way_split_is_disjoint_by_hull():
    tracks = _fleet(n=12) + _fleet(n=12, tag="G")
    for i, t in enumerate(tracks):
        t.mmsi = 2000 + i
    s = pe.hull_split_3way(tracks, seed=7)
    assert not (s.fit_hulls & s.dev_hulls)
    assert not (s.fit_hulls & s.test_hulls)
    assert not (s.dev_hulls & s.test_hulls)
    assert (len(s.fit_hulls) + len(s.dev_hulls) + len(s.test_hulls)
            == len({str(t.mmsi) for t in tracks}))
    assert (len(s.fit_tracks) + len(s.dev_tracks) + len(s.test_tracks)
            == len(tracks))


def test_a_hull_on_two_sides_of_the_split_is_refused():
    """Constructed directly, because this is the failure the whole protocol
    exists to prevent and an assertion that only runs when the shuffler
    misbehaves is an assertion nobody has tested."""
    with pytest.raises(ValueError, match="memorise"):
        pe.Split3(fit_hulls=frozenset({"a", "b"}),
                  dev_hulls=frozenset({"b"}), test_hulls=frozenset({"c"}),
                  fit_tracks=[], dev_tracks=[], test_tracks=[], seed=1)


def test_a_precision_on_a_handful_of_hulls_is_marked_as_noise():
    """ADR-042 §7's rule, enforced rather than remembered."""
    truth = pe.TruthLabels(families={"a": frozenset({"dark_transfer"})})
    s = pe.score_flags(["a", "b"], hulls=["a", "b", "c", "d"], truth=truth,
                       label="tiny")
    assert s.precision == pytest.approx(0.5)
    assert s.too_few_to_report
    lo, hi = s.precision_ci
    assert hi - lo > 0.5, "an interval on two hulls should be nearly useless"
    assert "*0.50*" in pe.format_scores([s])


def test_scoring_keeps_the_whole_population_in_the_denominator():
    """The base rate is over every hull scored, not over the flagged ones. The
    alternative silently redefines the denominator and makes every precision
    look better than it is."""
    truth = pe.TruthLabels(families={f"a{i}": frozenset({"spoofing"})
                                     for i in range(5)})
    hulls = [f"a{i}" for i in range(5)] + [f"n{i}" for i in range(45)]
    s = pe.score_flags(["a0", "n0"], hulls=hulls, truth=truth, label="x")
    assert s.n_hulls == 50
    assert s.n_positive == 5
    assert s.base_rate == pytest.approx(0.1)
    assert s.recall == pytest.approx(0.2)


def test_only_motion_expressed_families_are_counted_when_asked():
    truth = pe.TruthLabels(families={
        "motion": frozenset({"dark_transfer"}),
        "paper": frozenset({"paperwork"}),
    })
    hulls = ["motion", "paper", "clean"]
    both = pe.score_flags(hulls, hulls=hulls, truth=truth, label="all")
    only = pe.score_flags(hulls, hulls=hulls, truth=truth, label="motion",
                          families=pe.MOTION_EXPRESSED_FAMILIES)
    assert both.n_positive == 2
    assert only.n_positive == 1, (
        "a motion-only detector cannot see a paperwork mismatch, and scoring "
        "it against one measures the corpus's composition")


def test_wilson_is_not_the_normal_interval_at_the_edges():
    lo, hi = pe.wilson(10, 10)
    assert hi <= 1.0 and lo < 1.0, "an interval must not run past 1.0"
    assert lo < 0.75, "ten of ten is not proof of a precision above 0.75"
    lo2, hi2 = pe.wilson(100, 100)
    assert lo2 > lo, "a hundred of a hundred should say more than ten of ten"
