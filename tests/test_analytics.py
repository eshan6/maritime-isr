"""The two analytics: rename-then-gap, and graph connectivity.

Both are meant to be run against live landed data and both are expected to be
able to return **nothing**. These tests mostly check that a null result comes
back as a null result rather than as a crash, an exception, or a zero dressed up
as something else.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
T0 = datetime(2026, 6, 1, tzinfo=UTC)
AS_OF = datetime(2026, 7, 29, tzinfo=UTC)


def _load(name: str):
    """Load a tools/ script as a module — they are scripts, not a package."""
    path = REPO / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_tools_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rg = _load("analytic_rename_gap")


def _match(**over):
    r = {"vessel_id": "v1", "match_tier": "imo", "ship_name": "NEW NAME",
         "ofac_name": "SEA HARRIER", "ofac_ent_num": "9639",
         "sanctions_as_of": AS_OF}
    r.update(over)
    return r


# ==========================================================================
# name mismatch selection
# ==========================================================================

def test_only_imo_tier_rows_can_show_a_name_mismatch():
    """A name-tier match agrees on the name by construction; a bare call-sign
    match is too weak to build an identity claim on (ADR-018)."""
    rows = [
        _match(vessel_id="a"),
        _match(vessel_id="b", match_tier="name", ofac_name="NEW NAME"),
        _match(vessel_id="c", match_tier="call_sign"),
    ]
    got = {m["vessel_id"] for m in rg.name_mismatches(rows)}
    assert got == {"a"}


def test_a_normalisation_difference_is_not_a_mismatch():
    """'M/V Sea Harrier' and 'SEA HARRIER' are the same name."""
    rows = [_match(ship_name="M/V Sea Harrier", ofac_name="SEA HARRIER")]
    assert rg.name_mismatches(rows) == []


def test_a_missing_name_on_either_side_is_not_a_mismatch():
    """Absence is not disagreement."""
    assert rg.name_mismatches([_match(ship_name=None)]) == []
    assert rg.name_mismatches([_match(ofac_name=None)]) == []


# ==========================================================================
# the freshness split
# ==========================================================================

def test_a_gfw_record_newer_than_the_ofac_snapshot_is_the_evasion_bucket():
    rows = [_match(vessel_id="v1")]
    identity = {"v1": [{"valid_from": AS_OF + timedelta(days=1)}]}
    gfw_newer, ofac_newer, undated = rg.split_by_freshness(rows, identity)
    assert len(gfw_newer) == 1 and not ofac_newer and not undated


def test_an_older_gfw_record_is_the_clerical_bucket():
    rows = [_match(vessel_id="v1")]
    identity = {"v1": [{"valid_from": AS_OF - timedelta(days=200)}]}
    gfw_newer, ofac_newer, undated = rg.split_by_freshness(rows, identity)
    assert len(ofac_newer) == 1 and not gfw_newer and not undated


def test_a_vessel_with_no_dated_identity_lands_in_undated_not_in_a_story():
    """Undated rows must not be silently swept into the interesting bucket."""
    rows = [_match(vessel_id="v1")]
    gfw_newer, ofac_newer, undated = rg.split_by_freshness(rows, {"v1": []})
    assert len(undated) == 1 and not gfw_newer and not ofac_newer


def test_the_split_is_exhaustive():
    """Every mismatch lands in exactly one bucket — no row quietly disappears."""
    rows = [_match(vessel_id=f"v{i}") for i in range(5)]
    identity = {
        "v0": [{"valid_from": AS_OF + timedelta(days=1)}],
        "v1": [{"valid_from": AS_OF - timedelta(days=1)}],
        "v2": [],
        "v3": [{"valid_from": None}],
    }
    a, b, c = rg.split_by_freshness(rows, identity)
    assert len(a) + len(b) + len(c) == len(rows)


def test_the_latest_identity_interval_decides_freshness():
    """A vessel with old and new intervals is judged on the newest one."""
    rows = [_match(vessel_id="v1")]
    identity = {"v1": [{"valid_from": AS_OF - timedelta(days=300)},
                       {"valid_from": AS_OF + timedelta(days=2)}]}
    gfw_newer, _, _ = rg.split_by_freshness(rows, identity)
    assert len(gfw_newer) == 1


# ==========================================================================
# end to end, including the null result
# ==========================================================================

@pytest.fixture
def landed(tmp_path, monkeypatch):
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    def land(rows, table, keys, day):
        for r in rows:
            stamp_envelope(r, source_id="test",
                           source_ref=str(r.get("event_id") or r.get("vessel_id")),
                           acquired_at=r.get(day) or T0,
                           confidence=r.pop("confidence", None))
        land_table(rows, table=table, key_fields=keys, day_field=day)

    return land


def test_a_vessel_that_is_both_renamed_and_gap_flagged_is_reported(landed, capsys):
    landed([{"vessel_id": "v1", "record_kind": "registry",
             "ship_name": "NEW NAME", "valid_from": AS_OF + timedelta(days=1)}],
           "gfw_vessel_identity", ("vessel_id", "valid_from"), "valid_from")
    landed([{"event_id": "gap-1", "vessel_id": "v1", "start_time": T0,
             "gap_duration_hours": 30.0, "gfw_intentional_disabling": True}],
           "gfw_ais_gaps", ("event_id",), "start_time")
    landed([_match(is_finding=True, ofac_imo="9111228", ofac_program="IRAN",
                   confidence=0.95)],
           "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")

    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "AND gap-flagged: 1" in out
    assert "gap-1" in out
    assert "flagged intentional BY GFW, not by us" in out


def test_a_null_result_is_reported_as_zero_not_as_an_error(landed, capsys):
    """The expected outcome, and it must read as a finding about the data."""
    landed([{"vessel_id": "v1", "record_kind": "registry",
             "ship_name": "NEW NAME", "valid_from": T0}],
           "gfw_vessel_identity", ("vessel_id", "valid_from"), "valid_from")
    landed([{"event_id": "gap-1", "vessel_id": "v-other", "start_time": T0,
             "gfw_intentional_disabling": True}],
           "gfw_ais_gaps", ("event_id",), "start_time")
    landed([_match(confidence=0.95)], "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")

    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "AND gap-flagged: 0" in out
    assert "Zero. Reported as zero." in out


def test_unflagged_gaps_never_enter_the_cross_reference(landed):
    """GFW having no verdict is not GFW saying 'not intentional'."""
    landed([{"event_id": "g1", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": None},
            {"event_id": "g2", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": False},
            {"event_id": "g3", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": True}],
           "gfw_ais_gaps", ("event_id",), "start_time")
    got = rg.intentional_gaps()
    assert [g["event_id"] for g in got["v1"]] == ["g3"]


def test_it_refuses_to_run_without_matches(landed):
    """No matches landed is a setup error, not a null result — say so."""
    assert rg.main([]) == 1


# ==========================================================================
# the connectivity report
# ==========================================================================

gr = _load("graph_report")


def test_hop_sizes_exclude_the_node_itself_and_double_counting():
    adj = {"a": {"b", "c"}, "b": {"a", "d"}, "c": {"a"}, "d": {"b"}}
    assert gr._hop_sizes(adj, "a") == (2, 1), "d is 2-hop; a and its 1-hop are not"


def test_an_isolated_node_has_no_neighbourhood():
    assert gr._hop_sizes({}, "lonely") == (0, 0)


def test_the_report_names_the_densest_neighbourhood(tmp_path, monkeypatch, capsys):
    """The number the operator asked for before any frontend exists."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.graph import GraphStore
    from maritime_isr.graph import from_landed as fl
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    def land(rows, table, keys, day):
        for r in rows:
            stamp_envelope(r, source_id="test",
                           source_ref=str(r.get("event_id") or r.get("vessel_id")),
                           acquired_at=r.get(day) or T0,
                           confidence=r.pop("confidence", None))
        land_table(rows, table=table, key_fields=keys, day_field=day)

    land([{"vessel_id": f"v{i}", "record_kind": "registry",
           "ship_name": f"SHIP {i}", "flag": "IND", "valid_from": T0}
          for i in range(4)],
         "gfw_vessel_identity", ("vessel_id", "valid_from"), "valid_from")
    # v0 meets v1, v2, v3 — it is the hub
    land([{"event_id": f"e{i}", "vessel_id": "v0", "counterpart_vessel_id": f"v{i}",
           "start_time": T0, "confidence": 0.6} for i in (1, 2, 3)],
         "gfw_encounters", ("event_id",), "start_time")
    land([_match(vessel_id="v0", confidence=0.95),
          _match(vessel_id="v3", ofac_ent_num="9700", confidence=0.95)],
         "sanctioned_vessel_matches",
         ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")

    store = GraphStore(tmp_path / "g.db")
    try:
        fl.populate(store)
        gr.report(store, at=T0.timestamp())
    finally:
        store.close()
    out = capsys.readouterr().out
    assert "DENSEST SANCTIONED NEIGHBOURHOOD" in out
    assert fl.vessel_node_id("v0") in out
    assert "SHIP 0" in out
    assert "1-hop  : 3 vessel(s)" in out
    assert "with >= 3 encounter edges:    1" in out
    assert "detected no vessel" in out, "the attribution block is not optional"


# ==========================================================================
# the freshness split is degenerate on a single snapshot — say so
# ==========================================================================

def test_one_ofac_snapshot_makes_the_freshness_split_uninformative():
    """Measured on the live run: 53 of 53 landed in one bucket.

    `sanctions_as_of` is our download date, not OFAC's designation date, and a
    GFW identity interval always starts before we downloaded. The split can
    only answer one way, so it is not evidence.
    """
    rows = [_match(vessel_id=f"v{i}") for i in range(5)]
    usable, reason = rg.freshness_is_informative(rows)
    assert usable is False
    assert "ONE OFAC snapshot" in reason


def test_two_snapshots_make_it_informative():
    rows = [_match(vessel_id="a"),
            _match(vessel_id="b", sanctions_as_of=AS_OF - timedelta(days=30))]
    usable, reason = rg.freshness_is_informative(rows)
    assert usable is True
    assert "2 OFAC snapshots" in reason


def test_the_report_refuses_to_present_a_degenerate_split(landed, capsys):
    landed([{"vessel_id": "v1", "record_kind": "registry",
             "ship_name": "NEW NAME", "valid_from": T0}],
           "gfw_vessel_identity", ("vessel_id", "valid_from"), "valid_from")
    landed([_match(confidence=0.95)], "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")
    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "THE SPLIT ABOVE CARRIES NO SIGNAL" in out


# ==========================================================================
# a zero needs a denominator
# ==========================================================================

def test_the_gap_census_separates_null_from_explicitly_false(landed):
    """0 flagged out of 5 nulls and 0 out of 5 assessed mean different things."""
    landed([{"event_id": "g1", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": None},
            {"event_id": "g2", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": False},
            {"event_id": "g3", "vessel_id": None, "start_time": T0,
             "gfw_intentional_disabling": True}],
           "gfw_ais_gaps", ("event_id",), "start_time")
    c = rg.gap_flag_census()
    assert c == {"total": 3, "flagged_true": 1, "explicit_false": 1, "null": 1,
                 "with_vessel_id": 2}


def test_an_all_null_verdict_column_is_called_out_as_possible_mapping_bug(
        landed, capsys):
    landed([{"event_id": f"g{i}", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": None} for i in range(5)],
           "gfw_ais_gaps", ("event_id",), "start_time")
    landed([_match(confidence=0.95)], "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")
    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "Every landed gap has a NULL verdict" in out
    assert "AIS gap rows landed in total          : 5" in out


def test_an_empty_gap_table_is_a_missing_input_not_a_null_result(landed, capsys):
    landed([_match(confidence=0.95)], "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")
    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "missing-input condition, not a null result" in out


def test_a_zero_from_an_empty_side_is_labelled_a_weaker_null(landed, capsys):
    """'Both populations exist and do not overlap' is a real finding.
    'One side was empty' is not, and must not be reported as though it were."""
    landed([{"vessel_id": "v1", "record_kind": "registry",
             "ship_name": "NEW NAME", "valid_from": T0}],
           "gfw_vessel_identity", ("vessel_id", "valid_from"), "valid_from")
    landed([{"event_id": "g1", "vessel_id": "v1", "start_time": T0,
             "gfw_intentional_disabling": False}],
           "gfw_ais_gaps", ("event_id",), "start_time")
    landed([_match(confidence=0.95)], "sanctioned_vessel_matches",
           ("vessel_id", "ofac_ent_num", "match_tier"), "sanctions_as_of")
    assert rg.main([]) == 0
    out = capsys.readouterr().out
    assert "one side of this cross-reference is empty" in out.lower()
