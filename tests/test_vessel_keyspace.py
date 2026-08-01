"""One vessel key, exercised — not asserted to exist. ADR-022.

**The standing rule this file obeys** (STATE.md, and the H3 join guard it is
modelled on): an existence check is near-worthless. The keyspace defect passed
every existence check it could have had. `alerts.subject` was populated. Every
subject resolved to a row in `nodes` — 4 of 4, 100%. A test asserting "the alert
has a subject" or "the subject names a node" would have been green throughout.

What was actually wrong is only visible if you *traverse*: the node an alert
pointed at was a provisional stub with one self-referential edge, while the hull
carrying flag, owner, sanctions, port calls and encounters sat under a different
key. So every test here runs a real lookup and then asks what the result is
connected to. A hull with no edges is a failure even though it is a node.

The root cause was that `from_landed` published `id:name:*` identity nodes only
— 115 of them and zero `id:mmsi:*` — so `resolve_mmsi`'s lookup had nothing to
find and minted a twin. The decisive test is therefore `resolve_mmsi` returning
the populated hull, and it is the first one below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr.graph import from_landed as fl
from maritime_isr.graph.identity import resolve_mmsi, vessel_id
from maritime_isr.graph.store import GraphStore
from maritime_isr.ingest import landing
from maritime_isr.ingest.landing import land_table, stamp_envelope
from maritime_isr.schemas.keys import (identity_node_id, native_vessel_id,
                                       vessel_node_id)

T0 = datetime(2026, 6, 4, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 25, tzinfo=timezone.utc)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A two-hull corpus: one real, one synthetic, both with an MMSI."""
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)

    def identity(vessel_id_, mmsi, imo, name, flag, *, synthetic=False):
        r = dict(vessel_id=vessel_id_, record_kind="registry", mmsi=str(mmsi),
                 imo=str(imo), ship_name=name, flag=flag,
                 valid_from=T0, valid_to=T1)
        stamp_envelope(
            r,
            source_id="synthetic-scenario" if synthetic else "gfw-vessels",
            source_ref=f"{vessel_id_}:identity", acquired_at=T0,
            is_synthetic=synthetic)
        return r

    rows = [
        identity("d88dfa283-31bc", 419759000, 9164263, "REAL SHIP", "IND"),
        # The scenario generator writes its own entity id into this column,
        # which is what produced `vessel:gfw:vessel:spine`.
        identity("vessel:spine", 999000001, 1000006, "SPINE", "PAN",
                 synthetic=True),
    ]
    land_table(rows, table="gfw_vessel_identity",
               key_fields=("vessel_id", "record_kind", "mmsi", "ship_name",
                           "valid_from"),
               day_field="valid_from")

    store = GraphStore(tmp_path / "graph.sqlite")
    fl.populate(store)
    return store


# --------------------------------------------------------------------------
# the decisive test
# --------------------------------------------------------------------------

def test_resolve_mmsi_reaches_the_populated_hull(corpus):
    """An MMSI must land on the hull with the edges, not a provisional twin.

    This is the whole defect in one assertion. Before ADR-022 this returned
    `vessel:mmsi:419759000` — a real node, so a presence check passed — carrying
    one self-referential edge and the props `{"provisional": true}`. An analyst
    clicking an alert on it reached nothing.
    """
    at = (T0 + timedelta(days=10)).timestamp()
    vid = resolve_mmsi(corpus, 419759000, at=at)

    assert not vid.startswith("vessel:mmsi:"), (
        f"{vid} is a provisional stub — resolve_mmsi fell through instead of "
        f"finding the hull, which means the populator is not publishing "
        f"id:mmsi:* nodes again")
    assert vid == vessel_node_id("d88dfa283-31bc")

    # Traverse, do not merely resolve. A hull with no edges is not a hull.
    out = corpus.edges(vid)
    assert out, "resolved to a node with no outbound edges at all"
    kinds = {e.edge_type for e in out}
    assert "flagged-to" in kinds, (
        f"reached a node carrying only {kinds} — the flag edge lives on the "
        f"populated hull, so its absence means we landed on the wrong node")


def test_the_synthetic_hull_resolves_the_same_way(corpus):
    """The scenario corpus must exercise the identical path (ADR-019)."""
    at = (T0 + timedelta(days=10)).timestamp()
    vid = resolve_mmsi(corpus, 999000001, at=at)
    assert vid == vessel_node_id("vessel:spine") == "vessel:gfw:spine"
    assert corpus.edges(vid), "synthetic hull resolved to an edgeless node"


def test_no_provisional_stub_is_created_for_a_known_mmsi(corpus):
    """The stub is the symptom. Resolving a known MMSI must not mint one."""
    before = corpus._con.execute(
        "SELECT COUNT(*) FROM nodes WHERE node_id LIKE 'vessel:mmsi:%'"
    ).fetchone()[0]
    at = (T0 + timedelta(days=10)).timestamp()
    for mmsi in (419759000, 999000001):
        resolve_mmsi(corpus, mmsi, at=at)
    after = corpus._con.execute(
        "SELECT COUNT(*) FROM nodes WHERE node_id LIKE 'vessel:mmsi:%'"
    ).fetchone()[0]
    assert after == before, (
        f"{after - before} provisional stub(s) minted for MMSIs the graph "
        f"already knows — the shadow-node defect has returned")


def test_an_unknown_mmsi_still_gets_a_provisional_hull(corpus):
    """The fallback must survive. Not every ship is in a registry.

    Deleting the provisional path would be the wrong repair: a vessel genuinely
    unknown to GFW still needs somewhere to hang a track.
    """
    at = (T0 + timedelta(days=10)).timestamp()
    vid = resolve_mmsi(corpus, 636099999, at=at)
    assert vid == "vessel:mmsi:636099999"
    assert corpus.node(vid)["props"]["provisional"] is True


def test_an_mmsi_outside_its_interval_does_not_resolve(corpus):
    """Time scoping is load-bearing and must not be traded away for a hit rate.

    Measured on the scenario corpus: 102 of 103 MMSIs resolve to a hull, and the
    one that does not is a vessel's *second* MMSI probed before the swap. That
    is the identity model working — a track under a number the ship was not yet
    broadcasting must not be attributed to it — and a "fix" that pushed 103/103
    would have broken B1's phoenix and B4's zombie.
    """
    at = (T0 - timedelta(days=30)).timestamp()
    assert resolve_mmsi(corpus, 419759000, at=at).startswith("vessel:mmsi:")


# --------------------------------------------------------------------------
# one key, one definition
# --------------------------------------------------------------------------

def test_the_double_prefix_is_gone():
    assert vessel_node_id("vessel:spine") == "vessel:gfw:spine"
    assert vessel_node_id("d88dfa283-31bc") == "vessel:gfw:d88dfa283-31bc"
    # Idempotent: applying the rule twice must not change the answer.
    assert vessel_node_id(vessel_node_id("vessel:spine")) == "vessel:gfw:spine"
    assert native_vessel_id("vessel:vessel:spine") == "spine"


def test_both_sides_build_identity_ids_with_the_same_function():
    """The populator and the resolver must not hold two spellings.

    They did: one module wrote `id:name:*` from its own f-string while the other
    read `id:mmsi:*` from a different one, and the two never met.
    """
    from maritime_isr.graph import identity as ident_mod
    assert ident_mod.identity_id("mmsi", 419759000) == \
        identity_node_id("mmsi", 419759000)
    # str and int must not produce different nodes — Parquet round-trips change
    # the type of an identifier without changing its value.
    assert identity_node_id("mmsi", "419759000") == \
        identity_node_id("mmsi", 419759000) == \
        identity_node_id("mmsi", 419759000.0)


def test_vessel_id_fallback_routes_through_the_canonical_constructor():
    assert vessel_id(9164263) == "vessel:imo:9164263"
    assert vessel_id(None, 419759000) == "vessel:mmsi:419759000"


def test_identity_node_id_refuses_an_unknown_kind():
    """A silently-accepted new kind is how the two sides drift apart again."""
    with pytest.raises(ValueError, match="unknown identity kind"):
        identity_node_id("hull_number", 123)


def test_populator_publishes_the_key_identity_nodes(corpus):
    """`id:mmsi:*` and `id:imo:*` must exist, because the resolver reads them.

    Their absence — 115 name nodes, zero mmsi nodes — was the root cause.
    """
    kinds = {}
    for (nid,) in corpus._con.execute(
            "SELECT node_id FROM nodes WHERE node_type='identity'"):
        kinds[nid.split(":")[1]] = kinds.get(nid.split(":")[1], 0) + 1
    for kind in ("mmsi", "imo", "name"):
        assert kinds.get(kind), (
            f"no id:{kind}:* nodes were published; the graph holds {kinds}")


# --------------------------------------------------------------------------
# the flag, on EVERY node type — not just the ones that worked
# --------------------------------------------------------------------------

def test_is_synthetic_agrees_with_source_on_every_node_type(corpus):
    """ADR-019 makes this flag the only thing separating the two populations.

    It was wrong on the most important node type: all 114 scenario hulls landed
    as real because `add_vessels` omitted the argument and the column defaults
    to 0, while the identity and gap nodes beside them were flagged correctly.
    Any real-vs-synthetic vessel count taken before this is void.

    Checked per node *type* rather than in aggregate, because that is exactly
    how it hid: the totals looked plausible and one type was entirely wrong.
    """
    by_type: dict[str, set[bool]] = {}
    for ntype, syn in corpus._con.execute(
            "SELECT node_type, is_synthetic FROM nodes"):
        by_type.setdefault(ntype, set()).add(bool(syn))

    # The fixture lands one real hull and one synthetic one, so every type that
    # derives from a vessel must show BOTH values. A type showing only False is
    # the defect this test exists for.
    for ntype in ("vessel", "identity"):
        assert by_type.get(ntype) == {True, False}, (
            f"node_type={ntype!r} has is_synthetic values {by_type.get(ntype)}; "
            f"expected both, since the corpus holds one real and one synthetic "
            f"hull. A single value means the flag is not being propagated.")


def test_the_synthetic_hull_is_flagged_synthetic(corpus):
    syn = corpus._con.execute(
        "SELECT is_synthetic FROM nodes WHERE node_id=?",
        (vessel_node_id("vessel:spine"),)).fetchone()[0]
    assert syn == 1, "a scenario hull landed in the graph flagged as real data"


def test_the_real_hull_is_not_flagged_synthetic(corpus):
    syn = corpus._con.execute(
        "SELECT is_synthetic FROM nodes WHERE node_id=?",
        (vessel_node_id("d88dfa283-31bc"),)).fetchone()[0]
    assert syn == 0


def test_edges_inherit_the_flag_from_their_rows(corpus):
    """An edge between two synthetic hulls must not read as real evidence."""
    vid = vessel_node_id("vessel:spine")
    for e in corpus.edges(vid):
        assert e.is_synthetic, (
            f"{e.edge_type} from a synthetic hull is flagged real; every "
            f"real-vs-synthetic edge count is then wrong")


# --------------------------------------------------------------------------
# the migration, on a graph that already exists
# --------------------------------------------------------------------------

def test_migration_collapses_the_double_prefix_in_place(tmp_path):
    """Zero-recompute: the graph is rewritten, landed data is never read.

    The graph accumulates edge history that cannot be regenerated (CLAUDE.md
    §6), so rebuilding it from scratch is the destructive option here, not the
    safe one. Renaming a key changes no fact.
    """
    import sqlite3
    path = tmp_path / "graph.sqlite"
    store = GraphStore(path)
    store.upsert_node("vessel:gfw:vessel:spine", "vessel", dict(a=1),
                      is_synthetic=True)
    store.upsert_node("port:ind-sikka", "port", {})
    store.add_edge("docked-at", "vessel:gfw:vessel:spine", "port:ind-sikka",
                   t_start=T0.timestamp(), t_end=None, confidence=0.8,
                   observed_at=T0.timestamp(),
                   source="synthetic-scenario:gfw-events", source_ref="x",
                   is_synthetic=True)
    store._con.commit()
    del store

    migrated = GraphStore(path)
    assert migrated.node("vessel:gfw:spine") is not None
    assert migrated.node("vessel:gfw:vessel:spine") is None
    e = migrated.edges("vessel:gfw:spine")
    assert len(e) == 1 and e[0].dst == "port:ind-sikka", (
        "the edge did not follow the node rename — it is now dangling")
    assert migrated.node("vessel:gfw:spine")["is_synthetic"] is True


def test_migration_is_a_no_op_on_a_clean_graph(tmp_path):
    """A real-only host must pay one indexed scan and no writes."""
    path = tmp_path / "graph.sqlite"
    store = GraphStore(path)
    store.upsert_node("vessel:gfw:d88dfa283", "vessel", {})
    store._con.commit()
    assert store._migrate_vessel_keys() == 0


def test_migration_refuses_to_merge_two_histories(tmp_path):
    """When both spellings exist, leave them and let a human decide.

    Silently merging would pick a winner between two edge histories on a guess,
    which is the kind of quiet decision that makes a graph untrustworthy.
    """
    path = tmp_path / "graph.sqlite"
    store = GraphStore(path)
    store.upsert_node("vessel:gfw:spine", "vessel", dict(which="canonical"))
    store.upsert_node("vessel:gfw:vessel:spine", "vessel", dict(which="old"))
    store._con.commit()
    assert store._migrate_vessel_keys() == 0
    assert store.node("vessel:gfw:vessel:spine") is not None
