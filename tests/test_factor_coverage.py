"""Group F — do the factor classes Areas 2 and 3 added actually reach the list?

The IDEX brief sets one test on every area it asks for:

    After each area lands, the ranked Vessel of Interest list should visibly
    gain a new class of factor. If adding an area does not change what appears
    on that list, the area was built in isolation and needs wiring in before
    moving on.

Areas 2 and 3 failed it. `identity_contradiction`, `notable_activity` and
`vessel_interaction` were built, gated, narrated and wired, and all three fired
zero times — not because the detectors were wrong but because the corpus had
never contained the situations they were built for. This module is that test,
made executable, plus the precision half: every positive here is paired with a
decoy that shares its surface and must stay quiet.

**Why the fix was in the data and not the thresholds.** A rule loosened until it
fires has been fitted to the absence of evidence, and afterwards it is
indistinguishable from a working one. Not a single threshold moved for this
group; the constants in `tracks/activity.py`, `tracks/interactions.py` and
`anomaly/identity.py` are exactly as they were measured. What changed is that
the corpus now contains a hull broadcasting a malformed IMO, a hull running a
lawnmower pattern, and two hulls holding station on each other where somebody
can hear them.

See ADR-034 and `scenario/scenarios/group_f.py`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("_snapshot_pristine_corpus")


# ==========================================================================
# the identifier, in isolation — no corpus needed
# ==========================================================================

def test_break_check_digit_produces_a_number_that_fails_its_own_checksum():
    from maritime_isr.ingest.sanctions_match import imo_checksum_ok
    from maritime_isr.scenario.identifiers import (IMO_MAX, IMO_MIN,
                                                   break_check_digit, mint_imo)

    for serial in range(0, 40):
        good = mint_imo(serial)
        bad = break_check_digit(good)
        assert imo_checksum_ok(str(good)), "the minter must produce valid IMOs"
        assert not imo_checksum_ok(str(bad))
        assert bad != good
        # Still unreachable by a real hull: inside the reserved band *and* not
        # a well-formed IMO at all.
        assert IMO_MIN <= bad <= IMO_MAX
        assert bad // 10 == good // 10, "only the check digit may change"


def test_break_check_digit_refuses_a_number_outside_the_reserved_band():
    """A real hull's IMO must never be corrupted and re-landed.

    The whole safety argument for this function is that it stays inside a band
    no correctly-registered vessel occupies. Handed a real number it would
    manufacture a near-miss of somebody's actual hull, which is the false
    accusation `identifiers.py` exists to prevent.
    """
    from maritime_isr.scenario.identifiers import break_check_digit

    with pytest.raises(ValueError):
        break_check_digit(9074729)          # a well-formed Lloyd's-series IMO


# ==========================================================================
# the registry attestation
# ==========================================================================

def test_the_punctuation_decoy_is_not_a_contradiction():
    """"M.V. OCEAN TRADER." and "OCEAN TRADER" are one hull.

    F4 exists because a name check that compares raw strings fires on a large
    fraction of any honest corpus — registries carry prefixes, suffixes and
    stops that the air does not.
    """
    from maritime_isr.anomaly.identity import check_registry_consistency

    findings = check_registry_consistency(
        broadcast=dict(name="OCEAN TRADER"),
        registry=dict(name="M.V. OCEAN TRADER."))
    assert not [f for f in findings if f.is_contradiction], (
        "punctuation is not a different vessel")


def test_a_call_sign_disagreement_is_a_contradiction():
    from maritime_isr.anomaly.identity import check_registry_consistency

    findings = check_registry_consistency(
        broadcast=dict(call_sign="3ED9NC"), registry=dict(call_sign="9HA4271"))
    bad = [f for f in findings if f.is_contradiction]
    assert len(bad) == 1
    assert bad[0].confidence >= 0.6, (
        "a call sign is issued with the flag; disagreement is a strong signal")


def test_an_absent_field_is_not_a_disagreement():
    """Half a comparison is not evidence.

    Real registry records are sparse. Treating "we do not hold a call sign for
    her" as "her call sign is wrong" would fire on most of the fleet.
    """
    from maritime_isr.anomaly.identity import check_registry_consistency

    findings = check_registry_consistency(
        broadcast=dict(name="OCEAN TRADER", call_sign=None),
        registry=dict(name="OCEAN TRADER"))
    assert not [f for f in findings if f.is_contradiction]


# ==========================================================================
# the pipeline query that used to throw the second attestation away
# ==========================================================================

def test_current_identities_pairs_the_broadcast_row_with_the_registry_row():
    """The defect this test exists for was in the *query*, not the detector.

    One row per `vessel_id` collapses a hull's self-reported identity and her
    registry entry into whichever sorted first, so the consistency check was
    handed one attestation and could only ever answer "cannot check".
    """
    import tools.run_scenario_pipeline as pipe

    rows = [
        dict(vessel_id="vessel:a", record_kind="self_reported", mmsi="999000001",
             ship_name="AIRBORNE NAME", call_sign="AAA1", vessel_class="bulker",
             valid_from=2, valid_to=None),
        dict(vessel_id="vessel:a", record_kind="registry", mmsi="999000001",
             ship_name="REGISTERED NAME", call_sign="BBB2",
             vessel_class="bulker", valid_from=1, valid_to=None),
        dict(vessel_id="vessel:b", record_kind="self_reported", mmsi="999000002",
             ship_name="ONLY ONE", call_sign="CCC3", vessel_class="reefer",
             valid_from=1, valid_to=None),
    ]

    class _Reader:
        def rows(self, _sql):
            return [dict(r, _rn=1) for r in rows]

    out = {r["vessel_id"]: r for r in pipe._current_identities(_Reader())}

    assert out["vessel:a"]["ship_name"] == "AIRBORNE NAME", (
        "the subject of an identity check is what the vessel broadcast")
    assert out["vessel:a"]["registry"]["name"] == "REGISTERED NAME"
    assert out["vessel:a"]["registry"]["call_sign"] == "BBB2"

    # One attestation is not an error. It is an honest "cannot check".
    assert "registry" not in out["vessel:b"]


def test_a_hull_with_only_a_registry_row_still_yields_a_subject():
    """A corpus with no self-reported rows must not silently lose every hull.

    The real GFW corpus contains vessels with registry entries and no AIS
    static at all. Dropping them would make the identity checks quietly stop
    running on exactly the hulls a registry knows most about.
    """
    import tools.run_scenario_pipeline as pipe

    class _Reader:
        def rows(self, _sql):
            return [dict(vessel_id="vessel:c", record_kind="registry",
                         mmsi="999000003", ship_name="PAPER ONLY",
                         call_sign="DDD4", vessel_class="bulker",
                         valid_from=1, valid_to=None, _rn=1)]

    out = pipe._current_identities(_Reader())
    assert len(out) == 1
    assert out[0]["vessel_id"] == "vessel:c"
    assert "registry" not in out[0], (
        "a registry record cannot be evidence against itself")


# ==========================================================================
# the corpus — the brief's own test, made executable
# ==========================================================================

@pytest.fixture(scope="module")
def corpus_identities():
    from maritime_isr.api.reader import open_reader
    import tools.run_scenario_pipeline as pipe

    with open_reader() as r:
        if not r.has("gfw_vessel_identity"):
            pytest.skip("no landed corpus — run maritime-isr scenario generate")
        rows = pipe._current_identities(r)
    if not rows:
        pytest.skip("no identity rows landed")
    return {r["vessel_id"]: r for r in rows}


def test_every_hull_carries_a_second_attestation(corpus_identities):
    """Without one the consistency check has no denominator at all."""
    paired = [r for r in corpus_identities.values() if r.get("registry")]
    assert len(paired) >= 200, (
        f"only {len(paired)} of {len(corpus_identities)} hulls carry a "
        f"registry attestation — the check is back to 'cannot check'")


def test_the_identity_checks_now_have_positive_cases(corpus_identities):
    """F1-F3 fire; F4-F5 do not. Recall and precision in one assertion."""
    from maritime_isr.anomaly.identity import check_identity

    def contradictions(vid):
        row = corpus_identities.get(vid)
        if row is None:
            pytest.skip(f"{vid} not in this corpus")
        return [f for f in check_identity(
            mmsi=row.get("mmsi"), imo=row.get("imo"), flag=row.get("flag"),
            name=row.get("ship_name"), call_sign=row.get("call_sign"),
            vessel_class=row.get("vessel_class"),
            registry=row.get("registry")) if f.is_contradiction]

    f1 = contradictions("vessel:bad_imo_hull")
    assert any(f.check == "imo_check_digit" for f in f1), (
        "F1 broadcasts an IMO that fails its check digit and must be caught "
        "by the one identity test that is pure arithmetic")

    assert contradictions("vessel:wrong_call_sign"), "F2 must fire"
    assert contradictions("vessel:registry_renamed"), "F3 must fire"

    assert not contradictions("vessel:punctuation_twin"), (
        "F4 is the same hull spelled the way a registry spells her")


def test_the_vessel_class_decoy_stays_below_the_alert_gate(corpus_identities):
    """F5 disagrees, and the disagreement is not worth waking anybody for.

    Two records classifying one hull differently is a real disagreement — the
    check is right to notice it — and it is also the single weakest thing the
    identity rules can say, because "general cargo" and "bulker" are the same
    ship to some registries. So the finding exists and the *gate* is what keeps
    it off the queue. That is the graded design working, and it is a different
    claim from "the check did not fire", which is why this is its own test.
    """
    from maritime_isr.anomaly.identity import check_identity
    from maritime_isr.config import ANOMALY_THRESHOLDS

    row = corpus_identities.get("vessel:class_quibble")
    if row is None:
        pytest.skip("F5 not in this corpus")
    bad = [f for f in check_identity(
        mmsi=row.get("mmsi"), imo=row.get("imo"), flag=row.get("flag"),
        name=row.get("ship_name"), call_sign=row.get("call_sign"),
        vessel_class=row.get("vessel_class"),
        registry=row.get("registry")) if f.is_contradiction]
    assert bad, "the two records really do disagree about her type"
    assert {f.check for f in bad} == {"registry_vessel_class"}
    gate = ANOMALY_THRESHOLDS["identity_contradiction"]
    assert max(f.confidence for f in bad) < gate, (
        "a vessel-class quibble must not clear the alert gate on its own")


@pytest.fixture(scope="module")
def corpus_tracks():
    import pandas as pd
    from maritime_isr.api.reader import open_reader
    from maritime_isr.schemas.sources import AIS
    from maritime_isr.tracks.builder import build_tracks

    with open_reader() as r:
        if not r.has("ais_position"):
            pytest.skip("no landed corpus")
        pos = pd.DataFrame(r.rows("SELECT * FROM ais_position"))
    if pos.empty:
        pytest.skip("no AIS positions landed")
    tracks, _ = build_tracks(pos, source=AIS)
    return tracks


def _tracks_for(tracks, mmsi_of, key):
    want = str(mmsi_of(key))
    return [t for t in tracks if str(getattr(t, "mmsi", "")) == want]


@pytest.fixture(scope="module")
def mmsi_of(corpus_identities):
    def _lookup(key):
        row = corpus_identities.get(f"vessel:{key}")
        if row is None:
            pytest.skip(f"vessel:{key} not in this corpus")
        return row["mmsi"]
    return _lookup


def test_the_survey_pattern_now_has_a_positive_case(corpus_tracks, mmsi_of):
    """F6 runs a lawnmower; the rule must say so.

    This branch of the activity classifier had no positive case in the corpus
    after the thresholds were re-derived — it went from claiming 151 of 209
    tracks to claiming none, and neither number tells you whether it works.
    """
    from maritime_isr.tracks.activity import (classify_activity,
                                              classify_activity_segments)

    found = _tracks_for(corpus_tracks, mmsi_of, "survey_runner")
    if not found:
        pytest.skip("F6's hull landed no track in this corpus")
    verdicts = set()
    for t in found:
        verdicts.add(classify_activity(t).activity)
        verdicts |= {a.activity for a in classify_activity_segments(t)}
    assert "survey_pattern" in verdicts, (
        f"F6 mows a box of ocean in ten reciprocal legs and was classified "
        f"{sorted(verdicts)}")


def test_the_coastal_rotation_is_still_not_a_survey(corpus_tracks, mmsi_of):
    """F8 is the false positive the first rule made 151 times.

    Long legs and reciprocal turns, and a liner on her rotation. If this ever
    starts reading as a survey the fix has been undone.
    """
    from maritime_isr.tracks.activity import classify_activity

    found = _tracks_for(corpus_tracks, mmsi_of, "rotation_liner")
    if not found:
        pytest.skip("F8's hull landed no track in this corpus")
    for t in found:
        assert classify_activity(t).activity != "survey_pattern", (
            "a there-and-back rotation covers no area — it crosses one")


def test_erratic_manoeuvring_now_has_a_positive_case(corpus_tracks, mmsi_of):
    from maritime_isr.tracks.activity import (classify_activity,
                                              classify_activity_segments)

    found = _tracks_for(corpus_tracks, mmsi_of, "erratic_runner")
    if not found:
        pytest.skip("F7's hull landed no track in this corpus")
    verdicts = set()
    for t in found:
        verdicts.add(classify_activity(t).activity)
        verdicts |= {a.activity for a in classify_activity_segments(t)}
    assert "manoeuvring_erratically" in verdicts, (
        f"F7 alters course sixteen times inside a few miles at nine knots "
        f"and was classified {sorted(verdicts)}")


@pytest.fixture(scope="module")
def corpus_interactions(corpus_tracks):
    from maritime_isr.tracks.interactions import detect_interactions
    return detect_interactions(corpus_tracks)


def _between(interactions, mmsi_of, a_key, b_key):
    want = {str(mmsi_of(a_key)), str(mmsi_of(b_key))}
    out = []
    for i in interactions:
        pair = {str(i.track_id_a).split(":")[-1], str(i.track_id_b).split(":")[-1]}
        if want & pair == want or want <= {str(i.track_id_a), str(i.track_id_b)}:
            out.append(i)
    return out


def test_interactions_are_no_longer_empty(corpus_interactions):
    """The headline: this was zero, and zero is what the brief's test failed on.

    Deliberately an assertion about the corpus rather than about one pair —
    the point of the group is that the relationship rule has *something* to be
    measured against at all.
    """
    assert corpus_interactions, (
        "no interaction anywhere in the corpus — group F should have written "
        "three, and none of the thresholds moved to accommodate them")


def test_the_three_relationships_are_each_found(corpus_interactions):
    kinds = {i.kind for i in corpus_interactions}
    for kind in ("moving_in_company", "shadowing", "transfer_pattern"):
        assert kind in kinds, (
            f"{kind} has no positive case in the corpus; found {sorted(kinds)}")


def test_the_transfer_has_both_parties_transmitting(corpus_interactions):
    """The case the corpus could not supply before F11.

    Every other transfer here has a dark counterparty — that silence is what
    makes those scenarios findings, and it also meant the transfer branch of
    the interaction rule had no positive case and was, honestly, untested.
    """
    transfers = [i for i in corpus_interactions
                 if i.kind == "transfer_pattern"]
    assert transfers, "no transfer pattern found"
    assert any(i.min_separation_m <= 500.0 for i in transfers)


def test_the_lane_decoy_is_not_a_formation(corpus_interactions, mmsi_of):
    """F12: same lane, same course, wandering gap. Traffic, not a relationship.

    This is why the rule has a separation-stability test at all, and the decoy
    is what stops that test being quietly relaxed.
    """
    hits = _between(corpus_interactions, mmsi_of, "lane_mate_a", "lane_mate_b")
    assert not hits, (
        f"two ships on a route were called a relationship: "
        f"{[h.kind for h in hits]}")


# ==========================================================================
# the resampler defect that made an AIS-visible transfer unfindable
# ==========================================================================

def _stub_track(rows):
    """A minimal BuiltTrack-alike: what `resample_track` actually reads."""
    import numpy as np
    import pandas as pd

    df = pd.DataFrame(rows)
    df["quality"] = "ok"
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    class _T:
        points = df
        median_report_s = float(np.median(np.diff(
            df["ts"].astype("int64").to_numpy() // 10 ** 9)))
    return _T()


def test_a_stopped_vessel_is_not_deleted_by_the_resampler():
    """The defect: cadence varies within a track, the gap rule did not.

    ITU-R M.1371 has a Class A set report every 10 seconds under way and every
    3 minutes at anchor, and reception thinning multiplies both — so in this
    corpus a moving hull is heard every ~4 minutes and a stopped one every ~65.
    The gap allowance was built from the track's *median* interval, one number
    for a track whose cadence varies eighteen fold within itself, so the
    stopped half of a track was dropped from every resample-based analysis.

    Everything the product cares about at low speed went with it: loitering,
    anchoring, and a ship-to-ship transfer between two hulls that were both
    transmitting.
    """
    import pandas as pd
    from maritime_isr.tracks.features import resample_track

    t0 = pd.Timestamp("2026-07-09T20:00:00Z")
    rows = []
    # Two hours under way, heard every four minutes, covering 20 km.
    for i in range(30):
        rows.append(dict(ts=t0 + pd.Timedelta(minutes=4 * i),
                         lat=19.0 + 0.006 * i, lon=69.0, sog_kn=10.0))
    # Then nine hours stopped, heard once an hour, drifting 200 m each time.
    stop = rows[-1]["ts"]
    for i in range(1, 10):
        rows.append(dict(ts=stop + pd.Timedelta(hours=i),
                         lat=19.174 + 0.0018 * i, lon=69.0, sog_kn=0.4))

    df = resample_track(_stub_track(rows))
    stopped = df[df.t >= stop.timestamp()]
    assert len(stopped) > 50, (
        f"nine hours at anchor produced {len(stopped)} resampled samples — "
        f"the stopped half of the track has been deleted")


def test_the_resampler_still_refuses_a_gap_the_vessel_could_have_crossed():
    """The other half: a silent hull that could have gone anywhere still has
    no resampled presence.

    This is the guarantee the distance test must not cost. A vessel heard at
    ten knots, then not heard for an hour, could be eighteen kilometres away in
    any direction, and interpolating a straight line through that is a
    fabrication — which is exactly what an AIS gap means and why the gap rule
    exists at all.
    """
    import pandas as pd
    from maritime_isr.tracks.features import resample_track

    t0 = pd.Timestamp("2026-07-09T20:00:00Z")
    rows = [dict(ts=t0 + pd.Timedelta(minutes=4 * i),
                 lat=19.0 + 0.006 * i, lon=69.0, sog_kn=10.0)
            for i in range(20)]
    gap_start = rows[-1]["ts"]
    # Silent for an hour, and 18 km further on when she comes back.
    for i in range(20):
        rows.append(dict(ts=gap_start + pd.Timedelta(hours=1, minutes=4 * i),
                         lat=19.28 + 0.006 * i, lon=69.0, sog_kn=10.0))

    df = resample_track(_stub_track(rows))
    inside = df[(df.t > gap_start.timestamp() + 600)
                & (df.t < gap_start.timestamp() + 3000)]
    assert len(inside) == 0, (
        "an hour of silence at ten knots was interpolated across — the vessel "
        "could have been anywhere in an 18 km radius")
