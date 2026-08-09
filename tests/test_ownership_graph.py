"""The scenario ownership loader — organizations + owned-by/operated-by edges.

This is what turns the graph view from a lonely star into an ownership network
(the "shell-company convergence" the product exists to surface). It is guarded on
table presence, so a real-only corpus with no scenario ownership table skips it
and stays honestly star-shaped; here we feed it fabricated rows directly.
"""
from __future__ import annotations

import pytest

from maritime_isr.graph import GraphStore, from_landed

_ORGS = [
    {"org_id": "org:parent", "name": "Parent Holdings Ltd", "designated": True,
     "jurisdiction": "SYC", "is_synthetic": True, "source_id": "synthetic-scenario",
     "source_ref": "o1", "confidence": 0.9,
     "incorporated": "2020-01-01T00:00:00+00:00",
     "acquired_at": "2026-06-04T00:00:00+00:00"},
    {"org_id": "org:operator", "name": "Operator Pte Ltd", "designated": False,
     "jurisdiction": "SGP", "is_synthetic": True, "source_id": "synthetic-scenario",
     "source_ref": "o2", "acquired_at": "2026-06-04T00:00:00+00:00"},
]
_OWN = [
    {"src": "vessel:a", "dst": "org:operator", "edge_kind": "operated-by",
     "is_synthetic": True, "source_id": "synthetic-scenario", "source_ref": "e1",
     "valid_from": "2026-06-04T00:00:00+00:00", "confidence": 0.8},
    {"src": "vessel:b", "dst": "org:operator", "edge_kind": "operated-by",
     "is_synthetic": True, "source_id": "synthetic-scenario", "source_ref": "e2",
     "valid_from": "2026-06-04T00:00:00+00:00", "confidence": 0.8},
    {"src": "org:operator", "dst": "org:parent", "edge_kind": "owned-by",
     "is_synthetic": True, "source_id": "synthetic-scenario", "source_ref": "e3",
     "valid_from": "2026-06-04T00:00:00+00:00", "confidence": 0.8},
]


def test_ownership_loader_builds_shell_cluster(tmp_path, monkeypatch):
    g = GraphStore(tmp_path / "g.sqlite")
    from_landed.ensure_ontology(g)
    from_landed.ensure_authorities(g)

    monkeypatch.setattr(from_landed, "_table_present", lambda t: True)
    monkeypatch.setattr(from_landed, "read_table",
                        lambda t: {"scenario_organizations": _ORGS,
                                   "scenario_ownership": _OWN}.get(t, []))

    assert from_landed.add_organizations(g) == 2
    assert from_landed.add_ownership(g) == 3

    # two hulls operated by one operator = a shared-owner cluster
    op_in = {e.src for e in g.edges("org:operator", direction="in")
             if e.edge_type == "operated-by"}
    assert op_in == {"vessel:gfw:a", "vessel:gfw:b"}

    # operator rolls up to the parent (the shell chain)
    owned = [e for e in g.edges("org:operator", direction="out")
             if e.edge_type == "owned-by"]
    assert owned and owned[0].dst == "org:parent"

    # the designated parent carries a sanctioned-under edge to the SCENARIO
    # authority — never OFAC (ADR-019)
    su = [e for e in g.edges("org:parent", direction="out")
          if e.edge_type == "sanctioned-under"]
    assert su and su[0].dst == "authority:SCENARIO-SDN"

    # every ownership edge is flagged synthetic and sourced accordingly
    for e in g.edges("org:operator", direction="in"):
        assert e.is_synthetic and e.source.startswith("synthetic-scenario")
    g.close()


def test_ownership_loader_is_a_noop_without_the_tables(tmp_path, monkeypatch):
    """A real-only corpus has no scenario ownership table — the loader must add
    nothing rather than crash, keeping the real graph star-shaped."""
    g = GraphStore(tmp_path / "g.sqlite")
    from_landed.ensure_ontology(g)
    monkeypatch.setattr(from_landed, "_table_present", lambda t: False)
    assert from_landed.add_organizations(g) == 0
    assert from_landed.add_ownership(g) == 0
    assert g.n_nodes("organization") == 0
    g.close()
