"""Area 2 — predictive analysis of AIS tracks. ADR-032.

Four capabilities, four sections: declared-identity authenticity, activity
classification from motion, forward projection as an assertion, and per-area
behavioural baselines.

**Every test exercises a code path.** The identity checks are pure functions and
are driven with real identifier arithmetic — a genuine IMO number and a
deliberately corrupted one, a real MID against an agreeing and a disagreeing
flag — rather than with mocks, because the whole value of those two checks is
that they are arithmetic and a mock would be testing the mock.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from maritime_isr import baselines as bl
from maritime_isr.anomaly import identity as idc
from maritime_isr.schemas.sources import AIS, RADAR
from maritime_isr.tracks import activity as act
from maritime_isr.tracks import projection as proj


# ==========================================================================
# 1. declared identity
# ==========================================================================

def _valid_imo(prefix6: int) -> str:
    """Build a checksum-valid IMO, so the test uses real arithmetic."""
    d = [int(c) for c in f"{prefix6:06d}"]
    return f"{prefix6:06d}{sum(d[i] * (7 - i) for i in range(6)) % 10}"


def test_a_valid_imo_check_digit_passes():
    f = idc.check_imo(_valid_imo(907472))
    assert f.outcome == "ok" and not f.is_contradiction


def test_a_corrupted_imo_check_digit_is_caught():
    good = _valid_imo(907472)
    bad = good[:6] + str((int(good[6]) + 5) % 10)
    f = idc.check_imo(bad)
    assert f.is_contradiction
    assert f.confidence >= 0.85
    assert "check digit" in f.statement


def test_the_imo_check_rejects_most_random_numbers():
    """The claim in the docstring is ~90%. Verify it rather than assert it.

    If this ever drops, the arithmetic has been changed and the rule's whole
    justification — "a passing number is very unlikely to be a typo" — has gone
    with it.
    """
    rng = np.random.default_rng(7)
    trials = [f"{int(n):07d}" for n in rng.integers(0, 10_000_000, 4000)]
    rejected = sum(1 for t in trials if idc.check_imo(t).is_contradiction)
    assert 0.85 <= rejected / len(trials) <= 0.95


def test_a_short_imo_is_malformed_not_merely_wrong():
    f = idc.check_imo("12345")
    assert f.is_contradiction
    assert "seven" in f.statement


def test_an_absent_imo_is_not_checkable_rather_than_a_finding():
    f = idc.check_imo(None)
    assert f.outcome == "not_checkable"
    assert not f.is_contradiction, (
        "an absent identifier is a gap in the record; reporting it as a "
        "contradiction would fire on most of an honest corpus")


def test_a_parquet_float_imo_still_validates():
    """`9074729.0` is what a Parquet round-trip hands back for an int column.

    The same trap ADR-022 hit on the MMSI join. Without the `.0` strip, every
    check digit in the corpus fails at once and the rule reports a total
    identity collapse.
    """
    good = _valid_imo(907472)
    assert idc.check_imo(good + ".0").outcome == "ok"


def test_mmsi_prefix_agreeing_with_the_flag_passes():
    f = idc.check_mmsi_flag("419123456", "IND")
    assert f.outcome == "ok"


def test_mmsi_prefix_contradicting_the_flag_fires():
    f = idc.check_mmsi_flag("419123456", "PAN")
    assert f.is_contradiction
    assert f.confidence >= 0.8
    assert "IND" in f.statement and "PAN" in f.statement
    assert f.detail["mid"] == 419


def test_an_unallocated_mid_makes_no_claim():
    """The table is partial on purpose, and silence is the safe direction.

    999 is the block the scenario generator mints into (ADR-019), so this is
    also the guarantee that the whole synthetic fleet is not reported as an
    identity contradiction.
    """
    # An MID that is genuinely unallocated in the table.
    f = idc.check_mmsi_flag("299123456", "PAN")
    assert f.outcome == "not_checkable"
    assert f.detail["reason"] == "unknown_mid"


def test_the_synthetic_fleet_is_not_reported_as_an_identity_contradiction():
    """The measured collision, enforced.

    `scenario.identifiers` mints MMSIs into 999000000-999999999 because 999 is
    not an assignable MID; ITU-R M.585 separately reserves the prefix `99` for
    aids to navigation. Before `_is_project_reserved`, the two facts together
    made every one of the 222 scenario vessels a contradiction at 0.8 — one
    detector flooding the queue with the entire corpus.
    """
    for mmsi in ("999000012", "999000211", "999999999"):
        assert not idc.check_mmsi_form(mmsi).is_contradiction, mmsi
        assert not idc.check_mmsi_flag(mmsi, "PAN").is_contradiction, mmsi
        assert idc.mid_of(mmsi) is None
    # And a genuine AtoN is still caught — the exemption must be narrow.
    assert idc.check_mmsi_form("992351000").is_contradiction


def test_every_mid_maps_to_a_three_letter_flag_code():
    for mid, flag in idc.MID_TO_FLAG.items():
        assert 200 <= mid <= 799, f"MID {mid} is outside the allocated range"
        assert len(flag) == 3 and flag.isalpha() and flag.isupper(), (
            f"MID {mid} maps to {flag!r}, which is not an ISO-3166 alpha-3 "
            f"code — the flag column it is compared against carries alpha-3")


def test_reserved_mmsi_forms_are_caught():
    for mmsi, what in (("003580000", "coast station"),
                       ("111234567", "aircraft"),
                       ("992351000", "aid to navigation"),
                       ("981234567", "parent ship")):
        f = idc.check_mmsi_form(mmsi)
        assert f.is_contradiction, f"{mmsi} ({what}) should not pass as a hull"


def test_a_normal_mmsi_passes_its_form_check():
    assert idc.check_mmsi_form("419123456").outcome == "ok"


def test_a_reserved_mmsi_carries_no_mid():
    """Reading digits 1-3 of an AtoN MMSI would produce a nonsense country."""
    assert idc.mid_of("992351000") is None
    assert idc.mid_of("419123456") == 419


def test_registry_name_differences_ignore_vessel_prefixes():
    """"M/V SEA STAR" and "SEA STAR" are the same ship."""
    out = idc.check_registry_consistency(
        broadcast={"name": "M/V Sea Star"}, registry={"name": "SEA STAR"})
    name = next(f for f in out if f.check == "registry_name")
    assert name.outcome == "ok"


def test_a_call_sign_disagreement_outranks_a_name_disagreement():
    """A call sign is issued with the flag; a name changes on sale."""
    out = idc.check_registry_consistency(
        broadcast={"name": "ALPHA", "call_sign": "9XYZ1"},
        registry={"name": "BETA", "call_sign": "3ABC2"})
    by = {f.check: f for f in out}
    assert by["registry_call_sign"].confidence > by["registry_name"].confidence


def test_a_missing_side_is_not_a_disagreement():
    out = idc.check_registry_consistency(
        broadcast={"name": "ALPHA"}, registry={})
    assert all(not f.is_contradiction for f in out)


def test_check_identity_returns_every_outcome_not_only_failures():
    out = idc.check_identity(mmsi="419123456", imo=_valid_imo(907472),
                             flag="IND")
    outcomes = {f.outcome for f in out}
    assert "ok" in outcomes, (
        "a surface has to be able to say 'we looked and it was fine' as "
        "distinct from 'we did not look'")


def test_finding_ids_are_stable_across_registry_refreshes():
    a = idc.finding_id("vessel:gfw:x", "imo_check_digit")
    b = idc.finding_id("vessel:gfw:x", "imo_check_digit")
    assert a == b


# ==========================================================================
# 2. activity classification
# ==========================================================================

class _Track:
    """The minimum surface `activity` and `projection` read from a track.

    Deliberately a stand-in rather than a `BuiltTrack`: it proves those modules
    depend on position, speed and course over time and on *nothing else* — no
    MMSI, no vessel class, no sensor flag. That is the property Area 3 needs and
    a test built on a real AIS track could not demonstrate.
    """

    def __init__(self, rows, track_id="t1", source=AIS):
        self.points = pd.DataFrame(rows)
        self.points["quality"] = "ok"
        self.track_id = track_id
        self.track_key = track_id
        self.mmsi = None
        self.source = source


def _leg(t0, n, *, lat, lon, sog, cog, step_s=600, dlat=0.0, dlon=0.0):
    rows = []
    for i in range(n):
        rows.append(dict(
            ts=pd.Timestamp(t0, unit="s", tz="UTC") + pd.Timedelta(seconds=i * step_s),
            lat=lat + dlat * i, lon=lon + dlon * i, sog_kn=sog,
            cog_deg=cog, sigma_m=50.0))
    return rows


def test_a_steady_fast_track_is_transiting():
    rows = _leg(1_780_000_000, 40, lat=15.0, lon=68.0, sog=14.0, cog=90.0,
                dlon=0.035)
    a = act.classify_activity(_Track(rows))
    assert a.activity == "transiting"
    assert a.confidence > 0.5
    assert "passage" in a.reason


def test_a_stopped_vessel_inside_a_port_is_anchored_not_loitering():
    """Place is what separates the two, and the shared helper decides it."""
    rows = _leg(1_780_000_000, 40, lat=18.95, lon=72.84, sog=0.2, cog=10.0)
    a = act.classify_activity(_Track(rows))
    assert a.activity == "anchored"


def test_a_stopped_vessel_in_open_water_is_loitering():
    rows = _leg(1_780_000_000, 40, lat=14.0, lon=66.0, sog=0.4, cog=10.0)
    a = act.classify_activity(_Track(rows))
    assert a.activity in ("loitering", "drifting")
    assert a.activity != "anchored"


def test_a_short_track_is_unclassified_with_a_reason():
    rows = _leg(1_780_000_000, 3, lat=15.0, lon=68.0, sog=10.0, cog=90.0)
    a = act.classify_activity(_Track(rows))
    assert a.activity == "unclassified"
    assert a.confidence == 0.0
    assert "fix" in a.reason


def test_unclassified_is_an_answer_not_an_error():
    """A confident wrong activity costs more than an admitted gap."""
    assert "unclassified" in act.ACTIVITIES
    assert act.ACTIVITIES["unclassified"]


def test_activity_is_identical_on_radar_and_ais():
    """Area 3's requirement, enforced here rather than asserted in prose.

    *"The same behaviours should be recognisable whether the track came from
    radar or AIS. If they are not, that is a defect in the fusion core."*
    """
    rows = _leg(1_780_000_000, 40, lat=15.0, lon=68.0, sog=14.0, cog=90.0,
                dlon=0.035)
    ais = act.classify_activity(_Track(rows, source=AIS))
    radar = act.classify_activity(_Track(rows, source=RADAR))
    assert ais.activity == radar.activity
    assert ais.confidence == radar.confidence
    assert ais.track_source != radar.track_source, "the label should differ"


def test_a_coastal_rotation_is_not_called_a_survey():
    """The measured defect: 151 of 209 tracks were called surveys.

    A there-and-back voyage supplies long legs and reciprocal turns, which is
    what the first version of the rule asked for. A survey also has to go
    nowhere.
    """
    rows = (_leg(1_780_000_000, 60, lat=15.0, lon=68.0, sog=12.0, cog=90.0,
                 dlon=0.05)
            + _leg(1_780_040_000, 60, lat=15.0, lon=71.0, sog=12.0, cog=270.0,
                   dlon=-0.05))
    a = act.classify_activity(_Track(rows))
    assert a.activity != "survey_pattern"


def test_segments_produce_a_sequence_and_merge_like_neighbours():
    rows = (_leg(1_780_000_000, 60, lat=15.0, lon=68.0, sog=14.0, cog=90.0,
                 dlon=0.03)
            + _leg(1_780_100_000, 60, lat=18.95, lon=72.84, sog=0.1, cog=10.0))
    segs = act.classify_activity_segments(_Track(rows), window_hours=3.0)
    kinds = [s.activity for s in segs]
    assert "transiting" in kinds and "anchored" in kinds
    for a, b in zip(segs, segs[1:]):
        assert not (a.activity == b.activity), (
            "adjacent identical verdicts must merge — an operator reads "
            "'anchored for 11 hours', not eleven one-hour verdicts")


def test_the_dominant_activity_is_duration_weighted():
    rows = (_leg(1_780_000_000, 20, lat=15.0, lon=68.0, sog=14.0, cog=90.0,
                 dlon=0.03)
            + _leg(1_780_020_000, 200, lat=18.95, lon=72.84, sog=0.1,
                   cog=10.0))
    segs = act.classify_activity_segments(_Track(rows), window_hours=3.0)
    d = act.dominant_activity(segs)
    assert d is not None and d.activity == "anchored"


def test_activity_features_are_inspectable_alongside_the_verdict():
    rows = _leg(1_780_000_000, 40, lat=15.0, lon=68.0, sog=14.0, cog=90.0,
                dlon=0.035)
    a = act.classify_activity(_Track(rows))
    for key in ("sog_median", "turn_rate_deg_min", "straightness", "spread_m"):
        assert key in a.features, (
            "a classifier whose inputs cannot be seen is one nobody can "
            "argue with")


# ==========================================================================
# 3. forward projection
# ==========================================================================

def test_a_projection_lands_where_dead_reckoning_says():
    p = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                          made_at=0.0, valid_for=3600.0)
    assert p.lead_hours == pytest.approx(1.0)
    assert p.lon > 68.0 and abs(p.lat - 15.0) < 0.02, "due east"
    # 12 knots for one hour is 12 nm.
    from maritime_isr.tracks.projection import _hav_m
    assert _hav_m(15.0, 68.0, p.lat, p.lon) / 1852.0 == pytest.approx(12.0,
                                                                     rel=0.02)


def test_the_cone_grows_with_lead_time():
    near = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                             made_at=0.0, valid_for=3600.0)
    far = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                            made_at=0.0, valid_for=6 * 3600.0)
    assert far.radius_m > near.radius_m
    assert far.confidence < near.confidence, (
        "a projection reaching further ahead is less believable, and a "
        "confidence that did not say so would be lying by omission")


def test_the_cone_is_capped_by_physics():
    """No cone may be wider than the distance a hull could have covered."""
    from maritime_isr.config import MAX_FEASIBLE_SPEED_KN
    p = proj.project_from(lat=15.0, lon=68.0, sog_kn=1.0, cog_deg=90.0,
                          made_at=0.0, valid_for=600.0,
                          position_sigma_m=10_000_000.0)
    cap = MAX_FEASIBLE_SPEED_KN * 1852.0 * (600.0 / 3600.0)
    assert p.radius_m <= cap + 1.0


def test_a_vessel_on_her_predicted_track_is_not_a_departure():
    p = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                          made_at=0.0, valid_for=3600.0)
    assert proj.check_departure(p, lat=p.lat, lon=p.lon, at=3600.0) is None


def test_a_vessel_far_off_her_predicted_track_is_a_departure():
    p = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                          made_at=0.0, valid_for=3600.0)
    d = proj.check_departure(p, lat=p.lat + 1.0, lon=p.lon, at=3600.0)
    assert d is not None
    assert d.radii_outside > 1.0
    assert "departed from her own predicted track" in d.statement
    assert d.detail["cone_radius_nm"] > 0


def test_a_projection_carries_its_own_provenance():
    p = proj.project_from(lat=15.0, lon=68.0, sog_kn=12.0, cog_deg=90.0,
                          made_at=0.0, valid_for=3600.0, track_id="t1")
    row = p.as_dict()
    for key in ("made_at", "valid_for", "from", "radius_m", "confidence",
                "basis", "pipeline_version"):
        assert key in row, (
            "a projection is a first-class assertion, so it carries when it "
            "was made, what from, and how sure — not just a position")


def test_an_out_of_range_lead_is_refused_with_a_reason():
    rows = _leg(1_780_000_000, 40, lat=15.0, lon=68.0, sog=12.0, cog=90.0,
                dlon=0.03)
    t = _Track(rows)
    with pytest.raises(ValueError, match="floor"):
        proj.departures_along(t, lead_hours=0.05)
    with pytest.raises(ValueError, match="ceiling"):
        proj.departures_along(t, lead_hours=48.0)


def test_a_steady_leg_produces_no_departures():
    rows = _leg(1_780_000_000, 120, lat=15.0, lon=68.0, sog=12.0, cog=90.0,
                step_s=600, dlon=0.0331)
    ds = proj.departures_along(_Track(rows), lead_hours=1.0)
    assert ds == [], (
        "a vessel holding course and speed is exactly where dead reckoning "
        "says; anything else means the projection arithmetic is wrong")


def test_projection_is_not_a_registered_suspicion_factor():
    """The measured negative result, enforced. See `projection`'s docstring.

    Swept over the corpus, departure-from-own-track flagged 87-98% of the fleet
    at every operating point that flagged anything at all. Under ADR-004 it is
    therefore not promoted to a factor, and this test is what stops it being
    quietly added later without a fresh measurement.
    """
    from maritime_isr.assistant import catalog
    for kind in catalog.FACTOR_KINDS:
        assert "departure" not in kind and "projection" not in kind, (
            f"{kind} promotes track projection to a suspicion factor. It was "
            f"measured at 87-98% of the fleet; re-measure before re-adding.")


# ==========================================================================
# 4. per-area baselines
# ==========================================================================

def _positions(n_per_cell=200):
    """Two cells with genuinely different normals: a fast lane and an anchorage."""
    rows = []
    rng = np.random.default_rng(11)
    t0 = pd.Timestamp("2026-06-01", tz="UTC")
    for i in range(n_per_cell):
        rows.append(dict(vessel_id=f"v{i % 20}", lat=15.0, lon=68.0,
                         sog_kn=float(rng.normal(13.0, 1.0)), cog_deg=90.0,
                         ts=t0 + pd.Timedelta(hours=i), is_synthetic=True))
    for i in range(n_per_cell):
        rows.append(dict(vessel_id=f"a{i % 20}", lat=18.95, lon=72.84,
                         sog_kn=float(abs(rng.normal(0.3, 0.2))), cog_deg=10.0,
                         ts=t0 + pd.Timedelta(hours=i), is_synthetic=True))
    return pd.DataFrame(rows)


def test_baselines_differ_between_areas():
    """The entire point: normal is a local fact, not a global constant."""
    index = bl.BaselineIndex(bl.derive_baselines(_positions()))
    lane = index.at(15.0, 68.0)
    anchorage = index.at(18.95, 72.84)
    assert lane and anchorage and lane.usable and anchorage.usable
    assert lane.percentile("sog_kn", 95) > anchorage.percentile("sog_kn", 95) * 5


def test_a_thin_cell_is_insufficient_not_a_baseline():
    thin = _positions(n_per_cell=10)
    index = bl.BaselineIndex(bl.derive_baselines(thin))
    for b in index._by_cell.values():
        assert not b.usable
        assert "below the" in b.as_dict()["note"]


def test_is_unusual_is_three_valued():
    """True, False and None are different answers and a boolean loses one."""
    index = bl.BaselineIndex(bl.derive_baselines(_positions()))
    fast = bl.is_unusual(index, lat=18.95, lon=72.84, metric="sog_kn",
                         value=25.0)
    normal = bl.is_unusual(index, lat=18.95, lon=72.84, metric="sog_kn",
                           value=0.2)
    unwatched = bl.is_unusual(index, lat=-40.0, lon=10.0, metric="sog_kn",
                              value=25.0)
    assert fast and fast["unusual"] is True
    assert normal and normal["unusual"] is False
    assert unwatched is None, (
        "a cell we have never watched must answer 'cannot say', not 'normal' "
        "— otherwise every unmonitored patch of ocean reports as clean")


def test_an_insufficient_cell_cannot_say_rather_than_says_normal():
    index = bl.BaselineIndex(bl.derive_baselines(_positions(n_per_cell=10)))
    assert bl.is_unusual(index, lat=15.0, lon=68.0, metric="sog_kn",
                         value=99.0) is None


def test_coverage_reports_how_much_of_the_layer_can_answer():
    index = bl.BaselineIndex(bl.derive_baselines(_positions()))
    cov = index.coverage()
    assert cov["cells"] >= 2 and cov["usable"] >= 2
    assert 0.0 <= cov["fraction_usable"] <= 1.0
    assert cov["min_observations"] == bl.MIN_OBSERVATIONS


def test_the_baseline_grid_uses_the_shared_h3_helper():
    """ADR-015: no module hard-codes an integer resolution."""
    from maritime_isr import h3util
    assert bl.BASELINE_RES == h3util.R5
    assert h3util.R5 not in h3util.RESOLUTIONS, (
        "R5 is named for the baseline layer but must not be stamped on every "
        "landed row — that would be a schema change across the whole corpus")


def test_a_baseline_row_carries_its_evidence_count():
    for b in bl.derive_baselines(_positions()):
        d = b.as_dict()
        assert d["n_observations"] > 0
        assert "min_observations" in d, (
            "an operator must be able to see how much we saw before believing "
            "what we say normal is")


def test_course_concentration_handles_the_wraparound():
    """A percentile of a circular quantity is meaningless; concentration is not."""
    t0 = pd.Timestamp("2026-06-01", tz="UTC")
    rows = [dict(vessel_id="v", lat=15.0, lon=68.0, sog_kn=10.0,
                 cog_deg=(350.0 if i % 2 else 10.0),
                 ts=t0 + pd.Timedelta(hours=i), is_synthetic=True)
            for i in range(200)]
    b = bl.derive_baselines(pd.DataFrame(rows))[0]
    r = b.metrics["course_concentration"]["r"]
    assert r > 0.9, (
        "headings of 350 and 10 degrees are 20 degrees apart and should read "
        "as highly concentrated, not as opposite")
    assert math.isfinite(r)


def test_deriving_without_a_speed_column_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="missing"):
        bl.derive_baselines(pd.DataFrame([{"lat": 1.0, "lon": 2.0}]))


def test_deriving_from_nothing_is_empty_not_an_error():
    assert bl.derive_baselines(pd.DataFrame()) == []
    assert bl.derive_baselines(None) == []


# ==========================================================================
# 5. the detectors, driven end to end
#
# **A detector that has never fired anywhere is untested**, and both of these
# fired zero alerts on the scenario corpus for reasons that are correct
# (arithmetic identity checks are exempt on synthetic identifiers) and for one
# reason that was a defect (the activity gate was set above the maximum score
# the detector could produce). These tests drive each one to emit, so the path
# from a rule to a scored, evidenced alert on a real graph node is exercised
# rather than assumed.
# ==========================================================================

@pytest.fixture
def store(tmp_path):
    from maritime_isr.graph import GraphStore
    return GraphStore(tmp_path / "g.sqlite")


def _lawnmower(track_id="SYN-TEST:0001", t0=1_780_000_000):
    """A realistic survey track: short legs, turning often, going nowhere.

    **Sized from the measured fleet, not from intuition.** A merchant on a
    multi-port rotation turns every 4-6 hours (0.17-0.28 legs/hour measured); a
    vessel actually working a lawnmower turns every 30-60 minutes. The first
    version of this fixture ran 6 legs over 25 hours — 0.24 legs/hour, squarely
    in the merchant band — and it only passed because the threshold was wrong
    in the same direction. Twelve legs of 50 minutes is 1.2 legs/hour, which is
    what the behaviour actually looks like.
    """
    rows = []
    for leg in range(12):
        east = leg % 2 == 0
        rows += _leg(t0 + leg * 3_000, 6,
                     lat=14.0 + 0.02 * leg,
                     lon=(66.0 if east else 66.25),
                     sog=4.0, cog=(90.0 if east else 270.0),
                     step_s=600, dlon=(0.05 if east else -0.05))
    tr = _Track(rows, track_id=track_id)
    tr.has_identity = False
    return tr


def test_the_activity_gate_is_reachable_by_its_own_detector():
    """The measured defect: a gate above the detector's arithmetic ceiling.

    An activity alert scores `notability x classification confidence`, and the
    classifier caps its own confidence well below 1. The gate was set at 0.55
    by analogy with the detectors around it; the ceiling is 0.496. It fired
    zero alerts on the corpus — not because the picture was clean.
    """
    from maritime_isr.anomaly.library import NOTABLE_ACTIVITIES
    from maritime_isr.config import ANOMALY_THRESHOLDS

    gate = ANOMALY_THRESHOLDS["notable_activity"]
    # The classifier's own confidence ceiling for each behaviour, from
    # `tracks.activity.classify_activity` — `base * 0.8` for a survey pattern,
    # `base * 0.6` for erratic manoeuvring, where `base` saturates at 1.0.
    ceilings = {"survey_pattern": 0.8, "manoeuvring_erratically": 0.6}
    for activity, (notability, _metric) in NOTABLE_ACTIVITIES.items():
        ceiling = notability * ceilings[activity]
        assert ceiling >= gate, (
            f"{activity} can score at most {ceiling:.3f}, below the "
            f"{gate} gate — it can never alert, whatever the data shows")


def test_an_identity_contradiction_becomes_a_scored_evidenced_alert(store):
    from maritime_isr.anomaly.library import detect_identity_contradiction
    from maritime_isr.schemas.keys import vessel_node_id

    vid = "gfw-abc"
    store.upsert_node(vessel_node_id(vid), "vessel", {"name": "TEST HULL"})
    fired = detect_identity_contradiction(
        store,
        [{"vessel_id": vid, "mmsi": "419123456", "flag": "PAN",
          "imo": _valid_imo(907472), "ship_name": "TEST HULL",
          "valid_from": pd.Timestamp("2026-06-01", tz="UTC")}],
        source_ref="unit")
    assert len(fired) == 1, "an MMSI/flag contradiction must reach the queue"

    alert = store.alerts()[0]
    assert alert["anomaly_type"] == "identity_contradiction"
    assert alert["subject"] == vessel_node_id(vid)
    assert alert["evidence"], "no naked assertions — the chain is the product"
    assert any("419" in str(e["props"].get("statement", ""))
               for e in alert["evidence"])
    assert alert["props"]["n_contradictions"] >= 1


def test_a_clean_identity_raises_nothing(store):
    from maritime_isr.anomaly.library import detect_identity_contradiction
    from maritime_isr.schemas.keys import vessel_node_id

    store.upsert_node(vessel_node_id("gfw-ok"), "vessel", {})
    fired = detect_identity_contradiction(
        store,
        [{"vessel_id": "gfw-ok", "mmsi": "419123456", "flag": "IND",
          "imo": _valid_imo(907472),
          "valid_from": pd.Timestamp("2026-06-01", tz="UTC")}],
        source_ref="unit")
    assert fired == []


def test_a_hull_the_graph_does_not_hold_is_skipped_not_stubbed(store):
    """ADR-022: an alert on a node with no edges reaches a stub, not a vessel."""
    from maritime_isr.anomaly.library import detect_identity_contradiction

    fired = detect_identity_contradiction(
        store,
        [{"vessel_id": "never-seen", "mmsi": "419123456", "flag": "PAN",
          "valid_from": pd.Timestamp("2026-06-01", tz="UTC")}],
        source_ref="unit")
    assert fired == []
    assert store.alerts() == []


def test_a_survey_pattern_becomes_an_alert_with_its_reason(store):
    """Drives the activity detector end to end over a lawnmower track."""
    from maritime_isr.anomaly.library import detect_notable_activity

    track = _lawnmower()

    fired = detect_notable_activity(store, [track], source_ref="unit")
    assert fired, "a lawnmower pattern must be able to reach the queue"
    alert = next(a for a in store.alerts()
                 if a["anomaly_type"] == "notable_activity")
    assert alert["props"]["activity"] in ("survey_pattern",
                                          "manoeuvring_erratically")
    assert alert["props"]["reason"], "the alert must carry why, not just what"
    assert alert["evidence"][0]["props"]["sensor"]


def test_ordinary_transits_raise_no_activity_alerts(store):
    """Most vessels are transiting and none of them is a finding."""
    from maritime_isr.anomaly.library import detect_notable_activity

    rows = _leg(1_780_000_000, 200, lat=15.0, lon=68.0, sog=13.0, cog=90.0,
                dlon=0.02)
    track = _Track(rows, track_id="SYN-TEST:0002")
    track.has_identity = False
    assert detect_notable_activity(store, [track], source_ref="unit") == []


def test_a_baseline_travels_as_context_without_silencing_a_pattern(store):
    """The measured defect: the baseline scaled a score it had no bearing on.

    Comparing a survey pattern's median speed against the local speed
    distribution and halving the score where the speed was ordinary is a
    category error — the finding is the pattern, not the speed. It took three
    genuine survey patterns from ~0.37 to ~0.19 and dropped every one below the
    gate, so the detector reported a clean picture because it had been told to
    ignore what it found.
    """
    from maritime_isr.anomaly.library import detect_notable_activity
    from maritime_isr.graph import GraphStore

    def fresh_track(tid):
        return _lawnmower(track_id=tid)

    # A baseline in which 4 knots is entirely ordinary, built at the track's
    # OWN centroid — a baseline in a neighbouring H3 cell answers "cannot say"
    # and the test would pass while proving nothing.
    centre = act.classify_activity(_lawnmower())
    ts = pd.Timestamp("2026-06-01", tz="UTC")
    local = pd.DataFrame([
        dict(vessel_id=f"v{i % 20}", lat=centre.lat, lon=centre.lon,
             sog_kn=12.0, cog_deg=90.0,
             ts=ts + pd.Timedelta(hours=i), is_synthetic=True)
        for i in range(300)])
    index = bl.BaselineIndex(bl.derive_baselines(local))
    assert index.at(centre.lat, centre.lon) is not None

    detect_notable_activity(store, [fresh_track("SYN-A:1")], source_ref="unit")
    without = [a["score"] for a in store.alerts()]

    store2 = GraphStore(":memory:")
    detect_notable_activity(store2, [fresh_track("SYN-A:1")],
                            source_ref="unit", baselines=index)
    withbl = [a["score"] for a in store2.alerts()]

    assert without and withbl, "a survey pattern must survive a local baseline"
    assert withbl[0] == pytest.approx(without[0]), (
        "a pattern-defined activity must not be scaled by a speed baseline")
    assert store2.alerts()[0]["props"]["local_baseline"], (
        "the local distribution is still worth showing the watchkeeper")


def test_only_a_signature_metric_scales_a_score():
    """The rule that stops the category error recurring."""
    from maritime_isr.anomaly.library import NOTABLE_ACTIVITIES

    for activity, (notability, metric) in NOTABLE_ACTIVITIES.items():
        assert 0.0 < notability <= 1.0
        assert metric is None or isinstance(metric, str), (
            f"{activity} must name the metric its signature IS, or None — "
            f"a baseline may only scale a score it has a bearing on")


# ==========================================================================
# 6. measured accuracy against ground truth
#
# **The section that was missing, and its absence let a broken classifier ship.**
# Everything above exercises a code path; none of it asked whether the answers
# are *right*. Measured against the declared `vessel_class` — which is ground
# truth in the strict sense, because the generator built each track *from* the
# class — the first build scored **0 of 45 fishing vessels** and every one of
# its three `survey_pattern` claims was a merchant on a multi-port rotation.
#
# `vessel_class` is legitimate to read here: this is the evaluation harness,
# which runs after the pipeline, and CLAUDE.md §8 requires it on every model
# change. The classifier itself never sees it.
# ==========================================================================

#: Floors, not targets. Set below the measured figures so ordinary variation
#: does not fail the build, and high enough that a regression of the kind that
#: shipped last time cannot pass.
FISHING_RECALL_FLOOR = 0.75
FISHING_PRECISION_FLOOR = 0.85


@pytest.fixture(scope="module")
def classified_fleet():
    """Every AIS track, classified, beside the class it was generated from."""
    from maritime_isr.api import graph_service as gsvc
    from maritime_isr.api.reader import open_reader
    from maritime_isr.tracks.builder import build_tracks

    if not gsvc.graph_exists():
        pytest.skip("no landed corpus — run tools/run_scenario_pipeline.py")
    with open_reader() as r:
        if not r.has("ais_position") or not r.has("gfw_vessel_identity"):
            pytest.skip("corpus lacks AIS positions or vessel identity")
        pos = pd.DataFrame(r.rows("SELECT * FROM ais_position"))
        ident = {str(x["mmsi"]): x for x in r.rows(
            "SELECT mmsi, vessel_class FROM gfw_vessel_identity "
            "WHERE mmsi IS NOT NULL")}
    if pos.empty:
        pytest.skip("no AIS positions landed")
    tracks, _ = build_tracks(pos, source=AIS)
    out = []
    for t in tracks:
        cls = (ident.get(str(t.mmsi)) or {}).get("vessel_class") or "unknown"
        out.append((cls, act.classify_activity(t)))
    return out


def test_fishing_vessels_are_recognised_as_fishing(classified_fleet):
    """Measured 0 of 45 before the thresholds were derived from the fleet.

    `TURNY_MIN_DEG_MIN` was 4.0 while the fishing turn-rate p90 is 3.53 — the
    gate sat above the population it was meant to admit, so no trawler in the
    corpus could ever reach it.
    """
    fish = [a for cls, a in classified_fleet if cls == "fishing"]
    if len(fish) < 10:
        pytest.skip("too few fishing vessels in this corpus to measure")
    hit = sum(1 for a in fish if a.activity == "fishing")
    recall = hit / len(fish)
    assert recall >= FISHING_RECALL_FLOOR, (
        f"fishing recall {recall:.0%} ({hit}/{len(fish)}) is below the "
        f"{FISHING_RECALL_FLOOR:.0%} floor — the activity thresholds have "
        f"drifted away from the population they were measured against")


def test_the_fishing_class_is_not_claimed_on_merchants(classified_fleet):
    fish_calls = [cls for cls, a in classified_fleet if a.activity == "fishing"]
    if len(fish_calls) < 5:
        pytest.skip("too few fishing calls to measure precision")
    right = sum(1 for c in fish_calls if c == "fishing")
    precision = right / len(fish_calls)
    assert precision >= FISHING_PRECISION_FLOOR, (
        f"fishing precision {precision:.0%} ({right}/{len(fish_calls)}) is "
        f"below the {FISHING_PRECISION_FLOOR:.0%} floor")


def test_no_merchant_is_called_a_survey_pattern(classified_fleet):
    """The measured false-positive class, enforced.

    All three survey claims on the corpus were merchants — a product tanker at
    0.216 legs/hour, a bulker at 0.258, a general cargo at 0.174 — because
    `SURVEY_MIN_LEGS_PER_HOUR` was 0.1, below the merchant p90 of 0.281.
    """
    bad = [cls for cls, a in classified_fleet
           if a.activity == "survey_pattern"
           and cls in ("product_tanker", "bulker", "general_cargo", "Aframax",
                       "Suezmax", "VLCC", "reefer")]
    assert not bad, (
        f"{len(bad)} merchant(s) classified as running a survey pattern: "
        f"{collections_counter(bad)}. A multi-port rotation is not a lawnmower.")


def collections_counter(items):
    import collections as _c
    return dict(_c.Counter(items))


def test_merchants_on_passage_are_recognised_as_transiting(classified_fleet):
    """The commonest thing in the picture must be got right, or nothing else
    on the list can be trusted."""
    merch = [a for cls, a in classified_fleet
             if cls in ("product_tanker", "bulker", "general_cargo", "Aframax",
                        "Suezmax", "VLCC", "reefer")]
    if len(merch) < 20:
        pytest.skip("too few merchants to measure")
    transiting = sum(1 for a in merch if a.activity == "transiting")
    assert transiting / len(merch) >= 0.5, (
        f"only {transiting}/{len(merch)} merchants read as transiting")
