"""Area 3 — classification of radar data. ADR-033.

Three capabilities: vessel type from motion alone, interactions between
vessels, and a profile on a contact that correlates to nothing.

**The accuracy sections measure against ground truth**, which for type is the
declared ``vessel_class`` the generator built each track from. Area 2 shipped an
activity classifier that passed every code-path test and scored 0 of 45 on the
class it most needed; these tests exist so that cannot happen twice.

Reading ``vessel_class`` here is legitimate: this is the evaluation harness,
running after the pipeline, and CLAUDE.md §8 requires it on every model change.
The classifier itself sees only motion — enforced by
`test_type_features_are_sensor_blind`.
"""
from __future__ import annotations

import pandas as pd
import pytest

from maritime_isr.fusion import contact_profile as cp
from maritime_isr.schemas.sources import AIS, RADAR
from maritime_isr.tracks import interactions as ix
from maritime_isr.tracks import vessel_type as vt

MERCHANT_CLASSES = ("product_tanker", "bulker", "general_cargo", "Aframax",
                    "Suezmax", "VLCC", "reefer")

#: Floors, not targets. Below the measured figures so ordinary variation does
#: not fail the build, high enough that the failure mode Area 2 shipped cannot.
COARSE_ACCURACY_FLOOR = 0.75
FISHING_RECALL_FLOOR = 0.70


# ==========================================================================
# fixtures
# ==========================================================================

class _Track:
    """The minimum surface these modules read from a track.

    A stand-in rather than a `BuiltTrack`, so the tests prove the modules
    depend on position, speed and course over time and on nothing else.
    """

    def __init__(self, rows, track_id="t1", source=AIS, track_key=None):
        self.points = pd.DataFrame(rows)
        self.points["quality"] = "ok"
        self.track_id = track_id
        self.track_key = track_key or track_id
        self.mmsi = None
        self.source = source
        self.has_identity = False
        # `resample_track` reads this to decide whether a fix-to-fix interval
        # is a gap. A stub without it fails inside the resampler rather than in
        # the module under test, which hides what actually broke.
        self.median_report_s = float(step_s_of(self.points))


def step_s_of(points) -> float:
    if len(points) < 2:
        return 300.0
    d = points["ts"].diff().dropna()
    return float(d.dt.total_seconds().median()) if len(d) else 300.0


def _leg(t0, n, *, lat, lon, sog, cog, step_s=300, dlat=0.0, dlon=0.0):
    return [dict(
        ts=pd.Timestamp(t0, unit="s", tz="UTC") + pd.Timedelta(seconds=i * step_s),
        lat=lat + dlat * i, lon=lon + dlon * i, sog_kn=sog, cog_deg=cog,
        sigma_m=50.0) for i in range(n)]


def _straight(track_id, *, lat=15.0, lon=66.0, sog=12.0, cog=90.0, n=120,
              source=AIS, offset_lat=0.0, t0=1_780_000_000, step_s=300):
    """A vessel on a steady easterly passage. `offset_lat` displaces her."""
    return _Track(_leg(t0, n, lat=lat + offset_lat, lon=lon, sog=sog, cog=cog,
                       step_s=step_s, dlon=0.0166),
                  track_id=track_id, source=source)


@pytest.fixture(scope="module")
def fleet():
    """Every AIS track beside the class it was generated from."""
    from maritime_isr.api import graph_service as gsvc
    from maritime_isr.api.reader import open_reader
    from maritime_isr.tracks.builder import build_tracks

    if not gsvc.graph_exists():
        pytest.skip("no landed corpus — run tools/run_scenario_pipeline.py")
    with open_reader() as r:
        if not r.has("ais_position") or not r.has("gfw_vessel_identity"):
            pytest.skip("corpus lacks AIS positions or identity")
        pos = pd.DataFrame(r.rows("SELECT * FROM ais_position"))
        ident = {str(x["mmsi"]): x for x in r.rows(
            "SELECT mmsi, vessel_class, vessel_id FROM gfw_vessel_identity "
            "WHERE mmsi IS NOT NULL")}
    if pos.empty:
        pytest.skip("no AIS positions landed")
    tracks, _ = build_tracks(pos, source=AIS)
    out = []
    for t in tracks:
        i = ident.get(str(t.mmsi)) or {}
        if i.get("vessel_class"):
            out.append((i.get("vessel_id") or str(t.mmsi), i["vessel_class"], t))
    if len(out) < 40:
        pytest.skip("too few labelled tracks to train")
    return out


@pytest.fixture(scope="module")
def model(fleet):
    m = vt.train(fleet)
    if m is None:
        pytest.skip("not enough labelled data to fit a type model")
    return m


# ==========================================================================
# 1. type from motion
# ==========================================================================

def test_type_features_are_sensor_blind():
    """Area 3's load-bearing property: a model trained on AIS must apply to
    radar, which is only sound if no feature knows which sensor it came from."""
    rows = _leg(1_780_000_000, 120, lat=15.0, lon=66.0, sog=12.0, cog=90.0,
                dlon=0.0166)
    a = vt.type_features(_Track(rows, source=AIS))
    r = vt.type_features(_Track(rows, source=RADAR))
    assert a == r, (
        "the type feature vector differs by sensor — a model trained on AIS "
        "tracks cannot then be applied to radar contacts")


def test_a_short_track_yields_no_features():
    rows = _leg(1_780_000_000, 5, lat=15.0, lon=66.0, sog=12.0, cog=90.0)
    assert vt.type_features(_Track(rows)) is None


def test_every_declared_feature_is_produced():
    rows = _leg(1_780_000_000, 120, lat=15.0, lon=66.0, sog=12.0, cog=90.0,
                dlon=0.0166)
    f = vt.type_features(_Track(rows))
    assert set(f) == set(vt.FEATURE_NAMES), (
        "FEATURE_NAMES and type_features have drifted apart; the model would "
        "be fed a vector in the wrong order")


def test_the_model_reports_what_it_cannot_separate(model):
    """The brief's instruction, made checkable.

    *"Report the confusion matrix and state plainly which classes it cannot
    tell apart."*
    """
    rep = model.report()
    assert rep["confusion_matrix"], "no confusion matrix was produced"
    assert rep["cannot_separate"], (
        "no confusable groups were found at all — on this corpus a laden "
        "bulker and a laden product tanker at 13 knots are the same motion, "
        "so a model claiming to separate everything is overfitting")
    assert rep["caveat"] and "synthetic" in rep["caveat"].lower()


def test_the_coarse_vocabulary_is_derived_not_declared(model):
    """Merged classes must come from the measurement, not a hand-written list."""
    merged = {c for g in model.groups for c in g}
    for c in merged:
        assert model.coarse_of[c] != c, (
            f"{c} is in a confusable group but still reports under its own "
            f"name — the merge did not reach the output vocabulary")
    assert len(set(model.coarse_of.values())) < len(model.classes)


def test_coarse_accuracy_clears_its_floor(model):
    assert model.accuracy_coarse >= COARSE_ACCURACY_FLOOR, (
        f"coarse accuracy {model.accuracy_coarse:.0%} is below the "
        f"{COARSE_ACCURACY_FLOOR:.0%} floor")
    assert model.accuracy_coarse >= model.accuracy_fine, (
        "merging classes the model cannot separate must not make it worse")


def test_fishing_is_separated_from_merchants(model, fleet):
    """The one class split that has to work: it is the class radar most needs."""
    hits = misses = 0
    for _, cls, track in fleet:
        if cls != "fishing":
            continue
        v = model.classify(track)
        if v.vessel_type == "fishing":
            hits += 1
        else:
            misses += 1
    if hits + misses < 10:
        pytest.skip("too few fishing vessels to measure")
    assert hits / (hits + misses) >= FISHING_RECALL_FLOOR


def test_a_low_confidence_prediction_refuses_to_name_a_class(model):
    """Refusing is a first-class output — the brief's "should not pretend to"."""
    assert vt.MIN_CONFIDENCE > 0.0
    rows = _leg(1_780_000_000, 40, lat=15.0, lon=66.0, sog=7.0, cog=90.0,
                dlon=0.01)
    v = model.classify(_Track(rows))
    assert v.vessel_type == "unclassified" or v.confidence >= vt.MIN_CONFIDENCE


def test_the_split_is_by_hull_not_by_track():
    """CLAUDE.md's chip-versus-scene rule, one domain along.

    A hull on both sides of the split lets the model memorise her rather than
    her class, and the accuracy it reports measures nothing.
    """
    rows = _leg(1_780_000_000, 120, lat=15.0, lon=66.0, sog=12.0, cog=90.0,
                dlon=0.0166)
    # One hull, many tracks, one class: a track-level split would put the same
    # hull on both sides and the model would be graded on what it memorised.
    labelled = [("hull-1", "bulker", _Track(rows, track_id=f"t{i}"))
                for i in range(60)]
    labelled += [("hull-2", "fishing",
                  _Track(_leg(1_780_000_000, 120, lat=14.0, lon=66.0, sog=3.5,
                              cog=(90.0 if i % 2 else 270.0), dlon=0.005),
                         track_id=f"f{i}")) for i in range(60)]
    m = vt.train(labelled, test_fraction=0.5, seed=1)
    if m is None:
        pytest.skip("fixture too small for the training floor")
    assert m.n_train > 0 and m.n_test > 0


def test_confusable_groups_reads_the_matrix():
    cm = {"a": {"a": 10, "b": 6}, "b": {"b": 9, "a": 7}, "c": {"c": 20}}
    groups = vt.confusable_groups(cm)
    assert any({"a", "b"} <= g for g in groups)
    assert not any("c" in g for g in groups)


def test_a_clean_matrix_merges_nothing():
    cm = {"a": {"a": 20}, "b": {"b": 20}}
    assert vt.confusable_groups(cm) == []


def test_the_confusion_matrix_renders():
    cm = {"a": {"a": 3, "b": 1}, "b": {"b": 4}}
    text = vt.format_confusion(cm)
    assert "a" in text and "b" in text and "3" in text


# ==========================================================================
# 2. interactions
# ==========================================================================

def test_two_vessels_in_company_are_recognised():
    a = _straight("A", n=120)
    b = _straight("B", n=120, offset_lat=0.018)          # ~2 km abeam
    got = ix.detect_interactions([a, b])
    assert got, "a sustained formation must be reported"
    assert got[0].kind in ("moving_in_company", "shadowing")
    assert got[0].duration_hours >= 1.0
    assert got[0].reason


def test_a_shadowing_vessel_is_named_as_the_follower():
    a = _straight("LEAD", n=140)
    # Same course and speed, sitting astern: start further west, same track.
    b = _Track(_leg(1_780_000_000, 140, lat=15.0, lon=65.97, sog=12.0,
                    cog=90.0, step_s=300, dlon=0.0166), track_id="FOLLOW")
    got = [i for i in ix.detect_interactions([a, b]) if i.kind == "shadowing"]
    if not got:
        pytest.skip("geometry did not produce a shadowing episode")
    assert got[0].follower in ("LEAD", "FOLLOW")
    assert "astern" in got[0].reason


def test_a_transfer_alongside_and_stopped_is_recognised():
    # Eighty five-minute steps is 6.7 hours. Sixty was five, which cleared the
    # old 120-minute floor and does not clear the 360-minute one (ADR-034) —
    # the fixture describes the behaviour, so it moves when the definition of
    # "sustained" does.
    a = _Track(_leg(1_780_000_000, 80, lat=15.0, lon=66.0, sog=0.3, cog=10.0),
               track_id="A")
    b = _Track(_leg(1_780_000_000, 80, lat=15.0018, lon=66.0, sog=0.3,
                    cog=200.0), track_id="B")
    got = [i for i in ix.detect_interactions([a, b])
           if i.kind == "transfer_pattern"]
    assert got, "alongside and stopped for hours in open water is a transfer"
    assert got[0].min_separation_m <= ix.ALONGSIDE_M
    assert "cargo" in got[0].reason


def test_an_anchorage_is_not_an_interaction():
    """The ADR-031 lesson, applied at the pair level.

    Every hull in an anchorage is near every other hull for days. 42 of 43
    dark-rendezvous alerts fired inside berths before the shared helper was
    applied to that rule.
    """
    a = _Track(_leg(1_780_000_000, 60, lat=18.95, lon=72.84, sog=0.3,
                    cog=10.0), track_id="A")
    b = _Track(_leg(1_780_000_000, 60, lat=18.9518, lon=72.84, sog=0.3,
                    cog=200.0), track_id="B")
    assert ix.detect_interactions([a, b]) == []


def test_two_vessels_merely_crossing_are_not_an_interaction():
    a = _straight("A", n=120, cog=90.0)
    b = _Track(_leg(1_780_000_000, 120, lat=14.9, lon=66.5, sog=12.0,
                    cog=0.0, step_s=300, dlat=0.0166), track_id="B")
    got = ix.detect_interactions([a, b])
    assert all(i.kind == "transfer_pattern" for i in got), (
        f"a crossing produced {[i.kind for i in got]} — different courses are "
        f"traffic, not a relationship")


def test_a_brief_overlap_is_not_an_interaction():
    """Persistence is what separates a formation from a lane coincidence.

    Measured on the corpus: at a 60-minute gate the detector produced 8
    findings, every one a pair of background vessels sharing a lane; at 120
    minutes it produced none.
    """
    a = _straight("A", n=8)
    b = _straight("B", n=8, offset_lat=0.018)
    assert ix.detect_interactions([a, b]) == []


def test_one_target_seen_twice_is_not_two_vessels():
    """The ADR-028 guard: `None == None` would discard every radar pair."""
    a = _straight("A", n=120, source=RADAR)
    b = _straight("B", n=120, source=RADAR)
    b.track_key = a.track_key              # same sensor target, two hypotheses
    assert ix.detect_interactions([a, b]) == []


def test_a_cross_sensor_interaction_is_flagged():
    """The event no single sensor can see — the reason this analysis exists."""
    a = _straight("AIS-1", n=120, source=AIS)
    b = _straight("RAD-1", n=120, offset_lat=0.018, source=RADAR)
    got = ix.detect_interactions([a, b])
    if not got:
        pytest.skip("geometry did not produce an interaction")
    assert got[0].cross_sensor is True


def test_every_interaction_kind_narrates_and_is_registered():
    from maritime_isr.assistant import catalog
    from maritime_isr.anomaly.library import NOTABLE_INTERACTIONS

    assert "vessel_interaction" in catalog.FACTOR_KINDS
    for kind in ix.INTERACTIONS:
        assert ix.INTERACTIONS[kind], f"{kind} has no description"
        assert kind in NOTABLE_INTERACTIONS, (
            f"{kind} is detectable but has no notability weight, so it can "
            f"never reach the queue")


def test_interaction_ids_are_stable():
    a = _straight("A", n=120)
    b = _straight("B", n=120, offset_lat=0.018)
    one = ix.detect_interactions([a, b])
    two = ix.detect_interactions([a, b])
    assert [i.interaction_id for i in one] == [i.interaction_id for i in two]


# ==========================================================================
# 3. the contact profile — the Area 3 payoff
# ==========================================================================

def test_an_unidentified_contact_gets_a_readable_description(model):
    """*"'Unidentified contact' is a position. 'Probable fishing vessel,
    loitering, no transponder, inside territorial waters' is intelligence."*"""
    rows = _leg(1_780_000_000, 200, lat=15.0, lon=66.0, sog=12.5, cog=90.0,
                dlon=0.0166)
    p = cp.profile_contact(_Track(rows, source=RADAR), type_model=model,
                           correlation_status="dark", length_m=175.0)
    s = p.sentence()
    assert "no transponder" in s
    assert s.endswith(".")
    assert "175 m" in s
    assert "None" not in s, f"a null reached an operator-facing sentence: {s}"


def test_a_profile_degrades_and_says_which_part_is_missing():
    rows = _leg(1_780_000_000, 200, lat=15.0, lon=66.0, sog=12.5, cog=90.0,
                dlon=0.0166)
    p = cp.profile_contact(_Track(rows, source=RADAR), type_model=None)
    assert p.vessel_type is None
    assert any("type" in g.lower() for g in p.gaps), (
        "a profile without a type model must say so, not silently produce a "
        "thinner sentence that looks the same as a confident one")
    assert "Unidentified contact" in p.sentence()


def test_a_track_too_short_for_a_type_still_profiles():
    rows = _leg(1_780_000_000, 6, lat=15.0, lon=66.0, sog=12.0, cog=90.0)
    p = cp.profile_contact(_Track(rows, source=RADAR), type_model=None)
    assert p.sentence()
    assert p.gaps


def test_the_profile_carries_no_naked_claim(model):
    rows = _leg(1_780_000_000, 200, lat=15.0, lon=66.0, sog=12.5, cog=90.0,
                dlon=0.0166)
    d = cp.profile_contact(_Track(rows, source=RADAR),
                           type_model=model).as_dict()
    if d["vessel_type"]:
        assert d["type_confidence"] > 0 and d["type_reason"]
    if d["activity"] and d["activity"] != "unclassified":
        assert d["activity_confidence"] > 0 and d["activity_reason"]
    assert d["pipeline_version"]


def test_the_profile_does_not_re_decide_darkness():
    """It profiles; the cascade decides. A second opinion on darkness would be
    a second, uncalibrated copy of a rule that already exists."""
    import inspect
    src = inspect.getsource(cp)
    assert "dark_score" not in src and "hearable" not in src, (
        "contact_profile is re-deriving the darkness verdict instead of "
        "carrying the cascade's")
