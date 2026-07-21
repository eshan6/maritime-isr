"""Phase 4 acceptance tests. The ones that matter most: append-only
re-assertion, decay state-vs-event, migration zero-recompute, identity
change closure, 1/2-hop chains, cycle termination, and sanctions AS-OF
correctness (a delisted owner must not convict a later rendezvous)."""
import time

import pandas as pd
import pytest

from maritime_isr.graph import (GraphStore, current_mmsi, ensure_world,
                           fold_registry_snapshot, ingest_ownership,
                           ingest_sanctions, ownership_chains,
                           process_events, resolve_mmsi)

T0 = pd.Timestamp("2026-06-15", tz="UTC").timestamp()
DAY = 86400.0


@pytest.fixture
def g(tmp_path):
    store = GraphStore(tmp_path / "g.sqlite")
    yield store
    store.close()


def _org_world(g, sanction_valid_to=None):
    ensure_world(g)
    ingest_ownership(g, dict(
        organizations=[dict(name="BadCo", jurisdiction="XX"),
                       dict(name="ParentCo", jurisdiction="XX"),
                       dict(name="MidCo", jurisdiction="XX", parent="ParentCo")],
        vessel_owners=[dict(mmsi=111, org="BadCo"),
                       dict(mmsi=222, org="MidCo")]),
        source_ref="t", as_of=T0)
    ingest_sanctions(g, [dict(registry="OFAC", entry_id="S1", name="BadCo",
                              entry_type="entity", program="P",
                              valid_from_epoch=T0 - 30 * DAY,
                              valid_to_epoch=sanction_valid_to),
                         dict(registry="OFAC", entry_id="S2", name="ParentCo",
                              entry_type="entity", program="P",
                              valid_from_epoch=T0 - 30 * DAY,
                              valid_to_epoch=None)],
                     source_ref="t")


def _meet(g, mmsi_a, mmsi_b, t):
    va, vb = resolve_mmsi(g, mmsi_a, at=t), resolve_mmsi(g, mmsi_b, at=t)
    for s, d in ((va, vb), (vb, va)):
        g.add_edge("met-with", s, d, t_start=t, t_end=t + 1800,
                   confidence=0.9, observed_at=t, source="t", source_ref="t",
                   props=dict(encounter_id="E1"))
    g.emit("met_with", va, t, dict(counterpart=vb, encounter_id="E1"))
    g.emit("met_with", vb, t, dict(counterpart=va, encounter_id="E1"))


# ---------------- store contracts ----------------

def test_edge_requires_provenance(g):
    g.upsert_node("a", "vessel"); g.upsert_node("b", "organization")
    with pytest.raises(ValueError, match="provenance"):
        g.add_edge("owned-by", "a", "b", t_start=T0, confidence=.9,
                   source="", source_ref="", observed_at=T0)


def test_edge_validates_ontology(g):
    g.upsert_node("a", "vessel"); g.upsert_node("p", "port")
    with pytest.raises(ValueError, match="unknown edge type"):
        g.add_edge("teleported-to", "a", "p", t_start=T0, confidence=.9,
                   source="t", source_ref="t", observed_at=T0)
    with pytest.raises(ValueError, match="not allowed"):
        g.add_edge("owned-by", "a", "p", t_start=T0, confidence=.9,
                   source="t", source_ref="t", observed_at=T0)


def test_append_only_reassertion_latest_wins(g):
    g.upsert_node("v", "vessel"); g.upsert_node("o", "organization")
    g.add_edge("owned-by", "v", "o", t_start=T0, confidence=.6,
               observed_at=T0, source="t", source_ref="r1")
    g.add_edge("owned-by", "v", "o", t_start=T0, confidence=.9,
               observed_at=T0 + DAY, source="t", source_ref="r2")
    latest = g.edges("v", "owned-by")
    assert len(latest) == 1 and latest[0].source_ref == "r2"
    assert len(g.edges("v", "owned-by", history=True)) == 2   # nothing lost


def test_as_of_time_scope(g):
    g.upsert_node("v", "vessel"); g.upsert_node("o", "organization")
    g.add_edge("owned-by", "v", "o", t_start=T0, t_end=T0 + 10 * DAY,
               confidence=.9, observed_at=T0, source="t", source_ref="t")
    assert len(g.edges("v", "owned-by", as_of=T0 + 5 * DAY)) == 1
    assert len(g.edges("v", "owned-by", as_of=T0 + 20 * DAY)) == 0
    assert len(g.edges("v", "owned-by", as_of=T0 - DAY)) == 0


def test_decay_state_vs_event(g):
    g.upsert_node("v", "vessel"); g.upsert_node("o", "organization")
    g.upsert_node("w", "vessel")
    g.add_edge("owned-by", "v", "o", t_start=T0, confidence=.8,
               observed_at=T0, source="t", source_ref="t")
    g.add_edge("met-with", "v", "w", t_start=T0, t_end=T0 + 1800,
               confidence=.8, observed_at=T0, source="t", source_ref="t")
    own = g.edges("v", "owned-by")[0]
    met = g.edges("v", "met-with")[0]
    # owned-by half-life 365 d: at +365 d confidence halves
    assert g.edge_confidence(own, at=T0 + 365 * DAY) == pytest.approx(.4, rel=.02)
    # met-with is an event: a year later the fact hasn't faded
    assert g.edge_confidence(met, at=T0 + 365 * DAY) == pytest.approx(.8)


def test_migration_zero_recompute(g):
    g.upsert_node("v", "vessel"); g.upsert_node("o", "organization")
    g.add_edge("owned-by", "v", "o", t_start=T0, confidence=.9,
               observed_at=T0, source="t", source_ref="t")
    before_sum, before_n = g.edges_checksum(), g.n_edges()
    v_old = g.ontology_version()
    g.migrate_add_edge_type("insured-by", dict(
        src=["vessel"], dst=["organization"], half_life_days=180.0,
        kind="state"))
    g.add_edge("insured-by", "v", "o", t_start=T0, confidence=.8,
               observed_at=T0, source="t", source_ref="t")
    assert g.ontology_version() == v_old + 1
    assert g.n_edges() == before_n + 1
    # prior rows byte-identical: recompute the checksum over old rowids
    import hashlib, sqlite3
    con = sqlite3.connect(g.db_path)
    h = hashlib.sha256()
    for row in con.execute("SELECT * FROM edges WHERE rowid<=? ORDER BY rowid",
                           (before_n,)):
        h.update(repr(row).encode())
    assert h.hexdigest() == before_sum


# ---------------- identity ----------------

def _snap(as_of_days, vessels):
    return dict(as_of_epoch=T0 + as_of_days * DAY, vessels=vessels)


def test_identity_change_closes_and_events(g):
    ensure_world(g)
    fold_registry_snapshot(g, _snap(0, [dict(imo=900, mmsi=111,
                                             name="ALPHA", flag="PA")]),
                           source_ref="v1")
    ev = fold_registry_snapshot(g, _snap(15, [dict(imo=900, mmsi=111,
                                                   name="BETA", flag="KM")]),
                                source_ref="v2")
    assert {e["field"] for e in ev} == {"name", "flag"}
    ids = g.edges("vessel:imo:900", "identified-as")
    closed = {e.props["value"] for e in ids if e.t_end is not None}
    open_ = {e.props["value"] for e in ids if e.t_end is None}
    assert "ALPHA" in closed and "PA" in closed
    assert "BETA" in open_ and "KM" in open_ and 111 in open_


def test_mmsi_swap_resolves_by_time(g):
    ensure_world(g)
    fold_registry_snapshot(g, _snap(0, [dict(imo=901, mmsi=111,
                                             name="A", flag="PA")]),
                           source_ref="v1")
    fold_registry_snapshot(g, _snap(15, [dict(imo=901, mmsi=222,
                                              name="A", flag="PA")]),
                           source_ref="v2")
    # before the swap, 111 was hull 901; after, 222 is
    assert resolve_mmsi(g, 111, at=T0 + 5 * DAY) == "vessel:imo:901"
    assert resolve_mmsi(g, 222, at=T0 + 20 * DAY) == "vessel:imo:901"
    assert current_mmsi(g, "vessel:imo:901", at=T0 + 20 * DAY) == 222
    # an unknown MMSI gets a provisional entity, not a crash
    assert resolve_mmsi(g, 333, at=T0).startswith("vessel:mmsi:")


# ---------------- rules ----------------

def test_one_hop_chain_fires_with_evidence(g):
    _org_world(g)
    _meet(g, 999, 111, T0 + 5 * DAY)     # 999 meets BadCo's vessel
    out = process_events(g)
    alerts = [a for a in g.alerts()
              if a["subject"] == resolve_mmsi(g, 999, at=T0)]
    assert len(alerts) == 1
    chain = alerts[0]["evidence"]
    assert [c["edge"] for c in chain] == ["met-with", "owned-by",
                                          "sanctioned-under"]
    assert alerts[0]["confidence"] == min(c["confidence"] for c in chain)
    assert all(c["source"] and c["source_ref"] for c in chain)


def test_two_hop_chain_fires(g):
    _org_world(g)
    _meet(g, 998, 222, T0 + 5 * DAY)     # 222 → MidCo → ParentCo (listed)
    process_events(g)
    alerts = [a for a in g.alerts()
              if a["subject"] == resolve_mmsi(g, 998, at=T0)]
    assert len(alerts) == 1
    assert [c["edge"] for c in alerts[0]["evidence"]] == \
        ["met-with", "owned-by", "owned-by", "sanctioned-under"]


def test_cycle_terminates_no_alert(g):
    ensure_world(g)
    ingest_ownership(g, dict(
        organizations=[dict(name="LoopA", jurisdiction="X", parent="LoopB"),
                       dict(name="LoopB", jurisdiction="X", parent="LoopA")],
        vessel_owners=[dict(mmsi=444, org="LoopA")]), source_ref="t", as_of=T0)
    _meet(g, 997, 444, T0 + DAY)
    process_events(g)                      # must return, not spin
    assert g.alerts() == []


def test_sanction_as_of_no_retroactive_conviction(g):
    """The owner was DELISTED before the rendezvous — no alert. Time-scoped
    sanctions edges are the whole point of as-of dates."""
    _org_world(g, sanction_valid_to=T0 + 2 * DAY)   # BadCo delisted at T0+2d
    _meet(g, 996, 111, T0 + 5 * DAY)                # meeting 3 days later
    process_events(g)
    assert [a for a in g.alerts()
            if a["subject"] == resolve_mmsi(g, 996, at=T0)] == []


def test_eval_and_ledger(g, tmp_path):
    from maritime_isr.eval.graph import evaluate_graph, record_to_ledger
    from maritime_isr.eval.harness import latest_runs
    _org_world(g)
    _meet(g, 419500000, 111, T0 + 5 * DAY)
    process_events(g)
    truth = dict(expected_alerts=[dict(rule="sanctioned_owner_rendezvous",
                                       subject_mmsi=419500000)],
                 expected_identity_events=0)
    r = evaluate_graph(g, g.alerts(), truth, [], entity_coverage=1.0,
                       migration_pass=True, inject_fired=True)
    assert r.alert_precision == 1.0 and r.alert_recall == 1.0
    db = tmp_path / "l.sqlite"
    record_to_ledger(r, db_path=db)
    assert latest_runs(3, db_path=db)[0]["suite"] == "phase4_graph_synthetic"
