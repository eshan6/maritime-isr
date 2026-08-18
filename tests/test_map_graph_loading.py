"""The two loading defects behind "the graph is empty and the player vanishes".

Both were reported from the demo and both were about *when* data arrives rather
than whether it exists:

1. **The time scrubber appeared seconds late and vanished on every navigation.**
   Its window came from `/stats`, requested eighth in a burst of eight — past
   the browser's ~6-connection limit, so it queued behind `/tracks` (measured
   at 3.06s against the scenario corpus, 40x the next slowest call). The
   control was hidden until the window landed, and React Router unmounts the
   view on navigation, so the wait repeated every time.

2. **The Graph view opened empty.** It required picking from a dropdown of
   thousands, and most picks land on a lone node because GFW registry ownership
   covers ~1.3% of hulls here.

These tests cover the server halves: a cheap dedicated window endpoint, and a
seed list ranked by degree. The client halves — request ordering, the session
cache, and the scrubber staying mounted while disabled — were verified in a
real browser and are not reachable from pytest.
"""
from __future__ import annotations

import pytest

from maritime_isr.api import graph_service as gsvc, service


def test_corpus_window_returns_the_span_alone():
    w = service.get_corpus_window()
    assert set(w) == {"start", "end"}


def test_corpus_window_agrees_with_the_one_stats_reports():
    """Two endpoints, one fact. If they drift, the scrubber and the dashboard
    would disagree about what window the operator is looking at."""
    if not service.get_corpus_window()["start"]:
        pytest.skip("no corpus landed")
    assert service.get_corpus_window() == service.get_stats()["corpus_window"]


def test_corpus_window_is_cheaper_than_the_full_stats_sweep():
    """The whole reason for the split. `get_stats` scans every event table,
    groups the sanctions matches, counts scenes, measures length coverage and
    walks the graph; the scrubber needs two aggregates.

    Asserted as a generous ratio rather than an absolute time so it does not
    turn into a flaky benchmark on a loaded machine — the point is that the
    cheap call cannot silently grow into the expensive one.
    """
    import time

    service.get_corpus_window(), service.get_stats()   # warm
    t0 = time.perf_counter(); service.get_corpus_window(); cheap = time.perf_counter() - t0
    t0 = time.perf_counter(); service.get_stats(); full = time.perf_counter() - t0
    assert cheap < full, f"corpus_window {cheap:.3f}s not cheaper than stats {full:.3f}s"


# ---------------------------------------------------------------------------
# graph seeds
# ---------------------------------------------------------------------------

def _seeds():
    if not gsvc.graph_exists():
        pytest.skip("graph not populated — run tools/run_scenario_pipeline.py")
    s = gsvc.best_seeds(10)
    if not s:
        pytest.skip("graph has no edges")
    return s


def test_seeds_are_ordered_by_degree():
    degrees = [s["degree"] for s in _seeds()]
    assert degrees == sorted(degrees, reverse=True)


def test_every_seed_actually_has_edges():
    """A seed with no edges would open the view on a lone circle — the exact
    outcome auto-seeding exists to avoid."""
    assert all(s["degree"] > 0 for s in _seeds())


def test_seeds_carry_the_degree_so_a_caller_cannot_overstate_them():
    """`best available` and `well connected` are different claims on a corpus
    where ownership covers ~1.3% of hulls. The number travels so the UI can
    say which one it is showing."""
    for s in _seeds():
        assert isinstance(s["degree"], int)
        assert s["label"]
        assert s["id"].startswith("vessel:")


def test_seeds_are_vessels_only():
    assert all(s["id"].startswith("vessel:") for s in _seeds())


def test_a_seed_expands_to_a_real_neighbourhood():
    """End to end: the vessel the view opens on must actually produce a graph,
    otherwise auto-seeding just relocates the empty canvas."""
    best = _seeds()[0]
    nb = gsvc.neighbourhood(best["id"], hops=2)
    assert nb is not None
    assert len(nb["nodes"]) > 1, "seed produced a lone node"
    assert nb["edges"]


def test_best_seeds_is_empty_not_an_error_without_a_graph(monkeypatch, tmp_path):
    """A missing graph is 'nothing to seed', not a 500 — the view falls back to
    its own empty state and names the command to populate it."""
    monkeypatch.setattr(gsvc, "graph_path", lambda: tmp_path / "absent.sqlite")
    assert gsvc.best_seeds() == []


# ---------------------------------------------------------------------------
# the whole web
# ---------------------------------------------------------------------------

def _web():
    if not gsvc.graph_exists():
        pytest.skip("graph not populated — run tools/run_scenario_pipeline.py")
    g = gsvc.full_graph()
    if not g["nodes"]:
        pytest.skip("graph is empty")
    return g


def test_web_edge_count_agrees_with_the_dashboard():
    """The web and the stats panel must not report different graphs.

    The web now draws the ownership network by default and hides the context
    families, so it legitimately shows fewer edges than the dashboard counts.
    The agreement being checked is that **nothing is lost** — ask for every
    context family and the two numbers meet exactly.

    Filtering ended edges out instead was measured to drop 191 of 344 on the
    fixture graph — a view that quietly disagrees with every other number in
    the product is worse than one that shows too much.
    """
    g = gsvc.full_graph(context=list(gsvc.CONTEXT_EDGE_TYPES))
    if not g["nodes"]:
        pytest.skip("graph is empty")
    if g["truncated"]:
        pytest.skip("graph exceeds the cap; counts are a subset by design")
    with gsvc.open_graph() as store:
        dash = store.counts_by_synthetic()["edges_current"]
    assert len(g["edges"]) == dash["real"] + dash["synthetic"]


def test_the_default_web_is_the_ownership_network_not_everything():
    """The view is titled "ownership network" and used to be 10% ownership.

    Measured on the fixture graph: 1,334 current edges, of which 133 were
    `owned-by`/`operated-by` and 1,170 were context — 629 `identified-as`,
    313 `docked-at`, 228 `flagged-to`. Context is also what produced the
    crossings, since flags and ports are stars rather than links.
    """
    g = _web()
    kinds = {e["edge_type"] for e in g["edges"]}
    assert kinds <= set(gsvc.STRUCTURAL_EDGE_TYPES), (
        f"context edges leaked into the default web: "
        f"{kinds - set(gsvc.STRUCTURAL_EDGE_TYPES)}")


def test_a_context_family_can_be_switched_back_on():
    """Hidden must mean hidden, not dropped."""
    base = _web()
    withflag = gsvc.full_graph(context=["flag"])
    assert len(withflag["edges"]) > len(base["edges"])
    assert "flagged-to" in {e["edge_type"] for e in withflag["edges"]}
    # And only that family arrives.
    assert "docked-at" not in {e["edge_type"] for e in withflag["edges"]}


def test_hidden_and_truncated_are_reported_as_different_numbers():
    """An operator has to know whether a checkbox would bring something back.

    `matched_*` is what survived the type filter; the gap up to `total_*` is
    one checkbox away. The gap down to what was returned is the cap, and no
    control recovers it. Collapsing them would send someone hunting a switch
    that cannot help.
    """
    g = _web()
    assert g["matched_nodes"] <= g["total_nodes"]
    assert len(g["nodes"]) <= g["matched_nodes"]
    # The fixture graph is mostly context, so the filter must actually bite.
    assert g["matched_nodes"] < g["total_nodes"]


def test_a_node_with_no_ownership_edge_is_dropped_not_drawn_isolated():
    """100 of 226 fixture vessels carry no ownership edge at all.

    Ranking by TOTAL degree and then hiding some edge types would keep them —
    `flag:IND` has degree 156 and would win a place in the core while drawing
    nothing. A field of unconnected circles is not information.
    """
    g = _web()
    connected = {e["source"] for e in g["edges"]} | {e["target"] for e in g["edges"]}
    orphans = [n["id"] for n in g["nodes"] if n["id"] not in connected]
    assert not orphans, f"isolated nodes in the ownership web: {orphans[:5]}"


def test_an_unknown_context_family_is_ignored_rather_than_fatal():
    """This arrives from a query string; a stale bookmark must not 500."""
    assert gsvc.resolve_edge_types(["nope"]) == list(gsvc.STRUCTURAL_EDGE_TYPES)
    g = gsvc.full_graph(context=["nope"])
    assert {e["edge_type"] for e in g["edges"]} <= set(gsvc.STRUCTURAL_EDGE_TYPES)


def test_ended_edges_are_carried_and_flagged_not_dropped():
    """Invariant 3: an ended relationship may be shown, but never as current."""
    g = _web()
    for e in g["edges"]:
        assert "is_current" in e
        assert e["is_current"] == (e["t_end"] is None)


def test_every_edge_endpoint_is_a_node_that_was_returned():
    """A dangling edge would make cytoscape throw and blank the whole view."""
    g = _web()
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids


def test_truncation_is_reported_with_the_totals_it_was_drawn_from():
    """A partial web that looks whole is how a viewer concludes the dataset is
    sparser than it is."""
    g = gsvc.full_graph(limit=5)
    if g["total_nodes"] <= 5:
        pytest.skip("graph smaller than the test limit")
    assert g["truncated"] is True
    assert len(g["nodes"]) == 5
    assert g["total_nodes"] > len(g["nodes"])
    assert g["total_edges"] >= len(g["edges"])


def test_truncation_keeps_the_connected_core_not_an_arbitrary_slice():
    g = gsvc.full_graph(limit=5)
    if g["total_nodes"] <= 5:
        pytest.skip("graph smaller than the test limit")
    degrees = [n["degree"] for n in g["nodes"]]
    assert degrees == sorted(degrees, reverse=True)


def test_focus_is_a_node_that_is_actually_in_the_payload():
    """A focus id the client cannot find leaves the camera unmoved and the
    panel naming a vessel that is not on screen."""
    g = _web()
    assert g["focus"] in {n["id"] for n in g["nodes"]}


def test_focus_basis_states_the_claim_it_is_making():
    g = _web()
    assert g["focus_basis"]
    assert "connected" in g["focus_basis"]
    # It must never describe itself as a risk judgement.
    for word in ("suspicious", "risky", "dangerous", "dark"):
        assert word not in g["focus_basis"].lower()


def test_focus_prefers_a_designated_vessel_when_one_is_connected():
    nodes = [
        {"id": "vessel:a", "node_type": "vessel", "degree": 50, "props": {}},
        {"id": "vessel:b", "node_type": "vessel", "degree": 9,
         "props": {"designated": True}},
        {"id": "org:c", "node_type": "organization", "degree": 99, "props": {}},
    ]
    focus, basis = gsvc._pick_focus(nodes)
    assert focus == "vessel:b"
    assert "sanctioned" in basis


def test_focus_falls_back_through_vessel_then_any_node():
    plain = [{"id": "vessel:a", "node_type": "vessel", "degree": 3, "props": {}}]
    assert gsvc._pick_focus(plain)[0] == "vessel:a"
    org = [{"id": "org:c", "node_type": "organization", "degree": 9, "props": {}}]
    focus, basis = gsvc._pick_focus(org)
    assert focus == "org:c" and "node" in basis
    assert gsvc._pick_focus([]) == (None, None)


def test_full_graph_is_empty_not_an_error_without_a_graph(monkeypatch, tmp_path):
    monkeypatch.setattr(gsvc, "graph_path", lambda: tmp_path / "absent.sqlite")
    g = gsvc.full_graph()
    assert g["nodes"] == [] and g["edges"] == []
    assert g["truncated"] is False and g["focus"] is None


def test_full_graph_limit_is_bounded():
    """The cap exists because an in-browser force layout cannot draw the real
    graph; a URL must not be able to raise it arbitrarily."""
    g = gsvc.full_graph(limit=10_000)
    assert g["limit"] <= 5000


def test_seed_limit_is_bounded():
    """An unbounded limit reaching the UI would let a URL ask for the whole
    node table."""
    if not gsvc.graph_exists():
        pytest.skip("graph not populated")
    assert len(gsvc.best_seeds(1)) <= 1
    assert len(gsvc.best_seeds(10_000)) <= 100
