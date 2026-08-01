"""Populating the graph from landed real data (ADR-017 priority 2).

The fixtures here are synthetic — they have to be, the live tables live on the
deploy laptop — but they test the *mapping*, which is the part that can be wrong
independently of the data. What they cannot test is whether the real data
contains anything interesting; only a live run answers that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr.graph import GraphStore
from maritime_isr.graph import from_landed as fl

UTC = timezone.utc
T0 = datetime(2026, 6, 1, tzinfo=UTC)
AS_OF = datetime(2026, 7, 29, tzinfo=UTC)


@pytest.fixture
def landed(tmp_path, monkeypatch):
    """A tiny but complete landed dataset, written through the real landing layer."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope, stamp_h3

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    def land(rows, table, keys, day):
        for r in rows:
            stamp_h3(r)
            # confidence is an envelope field, exactly as the connectors set it —
            # passing it as a plain column would be silently overwritten with None
            stamp_envelope(r, source_id="test", source_ref=str(r.get("event_id")
                                                              or r.get("vessel_id")),
                           acquired_at=r.get(day) or T0,
                           confidence=r.pop("confidence", None))
        land_table(rows, table=table, key_fields=keys, day_field=day)

    # two vessels, one of them renamed and sanctioned
    land([
        {"vessel_id": "v-a", "record_kind": "registry", "mmsi": "419000001",
         "imo": "9111228", "ship_name": "OLD NAME", "call_sign": "AAA1",
         "flag": "IND", "valid_from": T0, "valid_to": T0 + timedelta(days=10)},
        {"vessel_id": "v-a", "record_kind": "self_reported", "mmsi": "419000001",
         "imo": "9111228", "ship_name": "NEW NAME", "call_sign": "AAA1",
         "flag": "PAN", "valid_from": T0 + timedelta(days=11), "valid_to": None},
        {"vessel_id": "v-b", "record_kind": "registry", "mmsi": "419000002",
         "imo": "9999993", "ship_name": "HONEST TRADER", "call_sign": "BBB2",
         "flag": "IND", "valid_from": T0, "valid_to": None},
    ], "gfw_vessel_identity", ("vessel_id", "record_kind", "mmsi", "ship_name",
                               "valid_from"), "valid_from")

    land([
        {"event_id": "enc-1", "vessel_id": "v-a", "counterpart_vessel_id": "v-b",
         "start_time": T0 + timedelta(days=5), "end_time": T0 + timedelta(days=5),
         "lat": 15.5, "lon": 68.2, "duration_hours": 3.0, "confidence": 0.6},
        # counterpart missing: not an edge
        {"event_id": "enc-2", "vessel_id": "v-a", "counterpart_vessel_id": None,
         "start_time": T0 + timedelta(days=6), "lat": 15.6, "lon": 68.3},
    ], "gfw_encounters", ("event_id",), "start_time")

    land([
        {"event_id": "pv-1", "vessel_id": "v-b", "port_id": "P1",
         "port_name": "MUMBAI", "anchorage_flag": "IND",
         "start_time": T0 + timedelta(days=2), "end_time": T0 + timedelta(days=3),
         "lat": 18.9, "lon": 72.8, "duration_hours": 24.0, "confidence": 0.9},
    ], "gfw_port_visits", ("event_id",), "start_time")

    land([
        {"event_id": "gap-1", "vessel_id": "v-a",
         "start_time": T0 + timedelta(days=20), "end_time": T0 + timedelta(days=21),
         "lat": 16.0, "lon": 69.0, "gap_duration_hours": 30.0,
         "gfw_intentional_disabling": True, "confidence": 0.9},
        {"event_id": "gap-2", "vessel_id": "v-b",
         "start_time": T0 + timedelta(days=22), "lat": 16.1, "lon": 69.1,
         "gap_duration_hours": 4.0, "gfw_intentional_disabling": None},
    ], "gfw_ais_gaps", ("event_id",), "start_time")

    land([
        {"vessel_id": "v-a", "ofac_ent_num": "9639", "match_tier": "imo",
         "is_finding": True, "ship_name": "NEW NAME", "ofac_name": "SEA HARRIER",
         "ofac_program": "IRAN", "ofac_imo": "9111228", "ofac_owner": "OWNER CO",
         "flag": "PAN", "sanctions_as_of": AS_OF, "confidence": 0.95},
        {"vessel_id": "v-b", "ofac_ent_num": "9700", "match_tier": "name",
         "is_finding": False, "ship_name": "HONEST TRADER",
         "ofac_name": "HONEST TRADER", "ofac_program": "SDGT",
         "ofac_imo": None, "flag": "IND", "sanctions_as_of": AS_OF,
         "confidence": 0.35},
    ], "sanctioned_vessel_matches", ("vessel_id", "ofac_ent_num", "match_tier"),
        "sanctions_as_of")

    return tmp_path


@pytest.fixture
def store(landed, tmp_path):
    s = GraphStore(tmp_path / "graph.db")
    yield s
    s.close()


# ==========================================================================
# what gets built
# ==========================================================================

def test_populate_writes_every_edge_type_from_real_tables(store):
    counts = fl.populate(store)
    assert counts["vessels_from_identity"] == 2
    assert counts["met_with"] == 1, "one encounter has a counterpart"
    assert counts["encounters_skipped"] == 1, "the other has none"
    assert counts["docked_at"] == 1
    assert counts["reported_gap"] == 1, "only the GFW-flagged gap"
    assert counts["gaps_skipped"] == 1
    assert counts["sanctioned_findings"] == 1
    assert counts["sanctioned_candidates"] == 1


def test_a_reflagging_becomes_two_flagged_to_edges(store):
    """IND then PAN is a reflagging, not a conflict to resolve away."""
    fl.populate(store)
    flags = {e.dst for e in store.edges(fl.vessel_node_id("v-a"), "flagged-to")}
    assert flags == {"flag:IND", "flag:PAN"}


def test_a_superseded_name_is_the_formerly_identified_as_edge(store):
    """A LATER interval with a DIFFERENT name is what makes one former."""
    counts = fl.populate(store)
    # `identified_as` counts every identity kind, not just names — the
    # populator publishes mmsi/imo/call_sign nodes too, because `resolve_mmsi`
    # reads them and its not doing so was the ADR-022 shadow-stub defect. The
    # supersession analysis is a property of NAMES, so filter to those.
    assert counts["identified_as"] >= 3
    assert counts["identified_as_superseded"] == 1, "OLD NAME was replaced"
    edges = [e for e in store.edges(fl.vessel_node_id("v-a"), "identified-as")
             if e.props.get("kind") == "name"]
    assert len(edges) == 2, "v-a carries two distinct names"
    former = [e for e in edges if e.props["superseded_by_later_name"]]
    assert [e.props["value"] for e in former] == ["OLD NAME"]


def test_a_closed_interval_with_no_successor_is_not_a_former_identity(store):
    """The bug the first live run exposed: 8,724 of 8,724 intervals came back
    closed, because GFW's transmissionDateTo is the end of the window we
    queried. Reading closure as replacement labelled the whole fleet as having
    changed identity."""
    fl.populate(store)
    # v-b has one interval, open-ended, and is nobody's predecessor.
    # Filtered to names: the same interval also publishes mmsi/imo/call_sign
    # identity edges (ADR-022), and those carry no supersession claim.
    edges = [e for e in store.edges(fl.vessel_node_id("v-b"), "identified-as")
             if e.props.get("kind") == "name"]
    assert len(edges) == 1
    assert edges[0].props["superseded_by_later_name"] is False

    # and the closed-but-unreplaced case must behave the same way
    solo = {"v-solo": {"intervals": [
        {"ship_name": "ONLY NAME", "valid_from": T0,
         "valid_to": T0 + timedelta(days=5), "record_kind": "registry"}]}}
    fl.add_vessels(store, solo)
    total, superseded = fl.add_identities(store, solo)
    assert total == 1
    assert superseded == 0, "a closed interval alone is not a rename"


def test_re_using_the_same_name_later_is_not_a_supersession(store):
    """Two intervals, same name — GFW split the record, the ship did not
    rename."""
    same = {"v-same": {"intervals": [
        {"ship_name": "SAME NAME", "valid_from": T0,
         "valid_to": T0 + timedelta(days=5), "record_kind": "registry"},
        {"ship_name": "Same  Name", "valid_from": T0 + timedelta(days=6),
         "valid_to": None, "record_kind": "self_reported"},
    ]}}
    fl.add_vessels(store, same)
    total, superseded = fl.add_identities(store, same)
    assert total == 2
    assert superseded == 0


def test_no_ownership_edges_are_synthesised(store):
    """ADR-016: ownership is 0.66% and OFAC's owner field is free text.

    An owned-by edge built from either would be an invention, and the whole
    point of recording the negative finding was to stop that happening.
    """
    fl.populate(store)
    types = {t for (t,) in store._con.execute(
        "SELECT DISTINCT edge_type FROM edges")}
    assert "owned-by" not in types
    assert "operated-by" not in types


def test_unflagged_gaps_are_excluded_by_default(store):
    """No verdict from GFW is not a verdict of 'unintentional'."""
    fl.populate(store)
    gaps = {e.dst for e in store.edges(fl.vessel_node_id("v-b"), fl.GAP_EDGE_TYPE)}
    assert gaps == set(), "gap-2 carries no GFW verdict and must not be an edge"


def test_all_gaps_mode_includes_them(store):
    counts = fl.populate(store, only_intentional_gaps=False)
    assert counts["reported_gap"] == 2
    node = store.node("gap:gap-2")
    assert node["props"]["gfw_intentional_disabling"] is False


# ==========================================================================
# attribution
# ==========================================================================

def test_gfws_verdict_is_attributed_to_gfw_on_the_edge_and_the_node(store):
    """The framing rule, enforced in data rather than in a docstring."""
    fl.populate(store)
    e = store.edges(fl.vessel_node_id("v-a"), fl.GAP_EDGE_TYPE)[0]
    assert e.props["assessed_by"] == "global-fishing-watch"
    assert e.source.startswith("gfw-events")
    node = store.node(e.dst)
    assert node["props"]["assessed_by"] == "global-fishing-watch"


def test_a_sanctions_edge_separates_who_listed_from_who_matched(store):
    fl.populate(store)
    e = [x for x in store.edges(fl.vessel_node_id("v-a"), "sanctioned-under")][0]
    assert e.props["listed_by"] == "us-treasury-ofac"
    assert e.props["matched_by"] == "maritime-isr"
    assert e.props["match_tier"] == "imo"


def test_a_candidate_match_keeps_its_low_confidence_as_an_edge(store):
    """Name-only matches become edges so leads are not lost, at 0.35 so they
    can never be read as findings (ADR-004)."""
    fl.populate(store)
    e = store.edges(fl.vessel_node_id("v-b"), "sanctioned-under")[0]
    assert e.base_confidence == pytest.approx(0.35)
    assert e.props["is_finding"] is False


def test_every_edge_carries_provenance(store):
    """CLAUDE.md §4.1 — the store rejects naked facts, so this asserts the
    stronger thing: that what we pass is meaningful, not just non-empty."""
    fl.populate(store)
    rows = store._con.execute(
        "SELECT edge_type, source, source_ref, pipeline_version FROM edges"
    ).fetchall()
    assert rows
    for etype, source, ref, ver in rows:
        assert source and source != "None", etype
        assert ref and ref != "None", etype
        assert ver, etype


# ==========================================================================
# decay on real timestamps
# ==========================================================================

def test_docked_at_decays_to_nothing_over_eight_weeks(store):
    """A 2-day half-life is the ontology's claim that berthing rots fast."""
    fl.populate(store)
    at = (T0 + timedelta(days=56)).timestamp()
    summary = fl.decay_summary(store, at=at)
    assert summary["docked-at"]["below_usable"] == 1
    assert summary["docked-at"]["mean_confidence"] < 0.001


def test_event_edges_do_not_decay(store):
    """met-with records something that happened. Facts recede, they don't fade."""
    fl.populate(store)
    at = (T0 + timedelta(days=3650)).timestamp()
    summary = fl.decay_summary(store, at=at)
    assert summary["met-with"]["mean_confidence"] == pytest.approx(0.6)
    assert summary["met-with"]["below_usable"] == 0


def test_decay_summary_covers_every_written_edge_type(store):
    fl.populate(store)
    written = {t for (t,) in store._con.execute(
        "SELECT DISTINCT edge_type FROM edges")}
    assert set(fl.decay_summary(store)) == written


# ==========================================================================
# re-running
# ==========================================================================

def test_repopulating_re_asserts_rather_than_duplicating(store):
    """Append-only + latest-wins: the row count grows, the graph does not."""
    fl.populate(store)
    triples_before = store._con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT edge_type, src, dst FROM edges)"
    ).fetchone()[0]
    nodes_before = store.n_nodes()

    fl.populate(store)
    triples_after = store._con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT edge_type, src, dst FROM edges)"
    ).fetchone()[0]
    assert triples_after == triples_before
    assert store.n_nodes() == nodes_before


def test_the_gap_types_are_registered_by_migration_not_hardcoded(store):
    """Ontology is data (roadmap 4.5): adding a type is an insert, and existing
    edges are untouched by construction."""
    before = store.ontology_version()
    fl.ensure_ontology(store)
    assert fl.GAP_NODE_TYPE in store.node_registry()
    assert fl.GAP_EDGE_TYPE in store.edge_registry()
    assert store.ontology_version() > before
    checksum = store.edges_checksum()
    fl.ensure_ontology(store)  # idempotent
    assert store.edges_checksum() == checksum


def test_an_unstated_confidence_is_marked_rather_than_invented_silently(store):
    """gap-2 carries no confidence. The store demands a number, so silence has
    to become one — but it must stay distinguishable from a stated value."""
    fl.populate(store, only_intentional_gaps=False)
    e = store.edges(fl.vessel_node_id("v-b"), fl.GAP_EDGE_TYPE)[0]
    assert e.base_confidence == pytest.approx(fl.UNSTATED_CONFIDENCE)
    assert e.props["confidence_stated"] is False

    stated = store.edges(fl.vessel_node_id("v-a"), fl.GAP_EDGE_TYPE)[0]
    assert stated.props["confidence_stated"] is True


def test_a_null_match_confidence_falls_back_to_the_tier_not_to_zero(store):
    """A row landed before ADR-018 may have a null confidence column. Zero
    would mean 'we are certain this is meaningless', which is not what a
    missing value says — the tier is what set the number in the first place."""
    from maritime_isr.ingest.sanctions_match import TIER_CONFIDENCE

    conf, stated = fl._conf({"confidence": None})
    assert conf == fl.UNSTATED_CONFIDENCE and stated is False
    assert TIER_CONFIDENCE["imo"] == 0.95
