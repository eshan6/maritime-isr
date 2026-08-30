"""The map and graph legibility pass — reported as "too cluttered and crowded".

Four defects, and three of them are the same defect wearing different clothes:
**a view asserting something it had not measured, or withholding something it
had.**

1. **`/tracks` truncated in silence.** It caps at `max_vessels` and returned
   `note: None` regardless, so a corpus of 210 vessels with positions was drawn
   as 200 with nothing anywhere saying which 200 or that ten were missing.
   `/events` has reported its cap since ADR-024; this endpoint was the one place
   still breaking the rule.

2. **Every identity edge on the graph read "identified as".** A vessel points at
   an MMSI, an IMO, a call sign and a name, and the canvas labelled all four
   with the one phrase they have in common — the phrase that carries no
   information. An IMO is welded to the hull; a name is paint. The edges already
   carried `props["kind"]`; nothing shipped it to the client.

3. **`GraphEdge` would have dropped the field anyway.** The neighbourhood route
   serialises through pydantic, which discards undeclared keys — so adding it to
   the service alone would have worked on `/graph/all` and silently not on
   `/vessels/{id}/neighbourhood`. That asymmetry is the kind that gets found
   three sessions later.

4. **Alert markers claimed a position they never had.** The marker was drawn at
   `events.find(e => e.vessel_id === subject)` — the vessel's earliest located
   event, because events arrive ordered by `start_time`. An alert raised last
   week could be pinned to a port call two months earlier.

The client halves — mark weights, the grouped key, where an alert marker lands —
are DOM and MapLibre paint properties and are not reachable from pytest; they
are verified in a browser. What is reachable is the data those halves consume,
which is what this module holds.
"""
from __future__ import annotations

import pytest

from maritime_isr.api import graph_service as gsvc, service
from maritime_isr.api.models import GraphEdge
from maritime_isr.graph.store import Edge
from maritime_isr.schemas.keys import IDENTITY_KINDS


def _edge(edge_type="identified-as", props=None):
    return Edge(edge_type=edge_type, src="vessel:gfw:x", dst="id:mmsi:419000000",
                t_start=0.0, t_end=None, base_confidence=0.9, observed_at=0.0,
                source="gfw-vessels", source_ref="ref", props=props or {})


# ---------------------------------------------------------------------------
# the identity kind on an edge
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", IDENTITY_KINDS)
def test_every_identity_kind_survives_the_gate(kind):
    """The populator writes these five and the canvas must be able to label all
    five. A kind that the gate silently dropped would put the generic
    "identified as" back on that edge with nothing reporting it."""
    assert gsvc._identity_kind(_edge(props={"kind": kind})) == kind


def test_an_unknown_identity_kind_falls_back_rather_than_reaching_the_canvas():
    """`IDENTITY_KINDS` is a closed vocabulary (ADR-022). A spelling outside it
    is a populator/UI drift, and rendering it raw as an edge label is how that
    drift would go unnoticed — so it degrades to the generic label instead."""
    assert gsvc._identity_kind(_edge(props={"kind": "gfw_tag"})) is None
    assert gsvc._identity_kind(_edge(props={})) is None
    assert gsvc._identity_kind(_edge(props={"kind": None})) is None


def test_only_identity_edges_carry_an_identity_kind():
    """`props["kind"]` is not exclusive to `identified-as` — a zone or registry
    edge may carry its own `kind` meaning something else entirely. Reading it
    off every edge type would label an ownership link "MMSI"."""
    assert gsvc._identity_kind(_edge("owned-by", {"kind": "mmsi"})) is None
    assert gsvc._identity_kind(_edge("entered-zone", {"kind": "imo"})) is None


def test_graph_edge_model_keeps_the_identity_kind():
    """The regression that would have been invisible: pydantic drops undeclared
    keys, so `/vessels/{id}/neighbourhood` would have served edges without the
    field while `/graph/all` served them with it — the same canvas labelling the
    same edge two different ways depending on which view opened it."""
    dumped = GraphEdge(source="a", target="b", edge_type="identified-as",
                       confidence=0.9, identity_kind="imo").model_dump()
    assert dumped["identity_kind"] == "imo"
    # and absent on the edge types that have none, rather than missing entirely
    plain = GraphEdge(source="a", target="b", edge_type="owned-by",
                      confidence=0.5).model_dump()
    assert plain["identity_kind"] is None


def test_served_identity_edges_are_labelled_by_kind():
    """End to end on a landed graph: every `identified-as` edge that reaches the
    web payload carries the kind it asserts.

    `identified-as` is a CONTEXT family (`CONTEXT_EDGE_TYPES`), excluded from
    the default web because almost every hull has several and they wreck the
    layout. So the context has to be requested here exactly as the view
    requests it — asking for the default graph and finding no identity edges
    would make this test pass by looking at the wrong thing.
    """
    if not gsvc.graph_exists():
        pytest.skip("no graph landed")
    web = gsvc.full_graph(limit=2000, context=["identity"])
    ident = [e for e in web["edges"] if e["edge_type"] == "identified-as"]
    if not ident:
        pytest.skip("no identity edges in this graph")
    assert all("identity_kind" in e for e in ident)
    kinds = {e["identity_kind"] for e in ident} - {None}
    assert kinds, "every identity edge came back unlabelled — props['kind'] is not reaching the payload"
    assert kinds <= set(IDENTITY_KINDS)


# ---------------------------------------------------------------------------
# /tracks reports its cap
# ---------------------------------------------------------------------------

def test_tracks_always_reports_how_many_vessels_it_matched():
    """`matched_vessels` is the true count of vessels with positions, present
    whether or not the cap bit. Without it the caller cannot tell a full answer
    from a capped one, which is the whole defect."""
    r = service.list_tracks(max_points=20)
    assert "matched_vessels" in r and "truncated" in r
    assert isinstance(r["matched_vessels"], int)


def test_tracks_reports_the_cap_when_it_bites():
    r = service.list_tracks(max_vessels=1, max_points=20)
    if r["matched_vessels"] < 2:
        pytest.skip("corpus has fewer than two vessels with positions")
    assert r["truncated"] is True
    assert r["note"] and "truncated" in r["note"].lower()
    # the true total is stated, not just the fact of truncation — "some are
    # missing" without "of how many" is what makes a capped layer unreadable
    assert f"{r['matched_vessels']:,}" in r["note"]
    assert len(r["items"]) <= 1


def test_nothing_dropped_means_no_note_and_decimation_is_not_truncation():
    """The other half, and the one that keeps the note worth reading: truncation
    is measured against what the CAP dropped, not against what the decimator
    skipped.

    A vessel with a single position cannot be interpolated and is legitimately
    absent from `items`, so `len(items)` can sit below `matched_vessels` with
    nothing wrong. Deriving the flag from that difference instead of from the
    cap would fire the warning on every corpus that has one such vessel — and a
    warning that is always on is one the operator stops reading, which is how
    the truncation note stopped meaning anything the first time.
    """
    r = service.list_tracks(max_vessels=100_000, max_points=20)
    if not r["items"]:
        pytest.skip("no AIS positions landed")
    # The cap is far above any corpus here, so nothing was capped — regardless
    # of how many vessels fell out of `items` for having too few points.
    assert r["truncated"] is False, (
        "a cap of 100,000 dropped nothing; any shortfall in `items` is "
        "decimation, not truncation")
    assert r["note"] is None
