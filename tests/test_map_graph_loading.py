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

3. **The player ran and nothing on the map moved.** The scrubber took its span
   from the corpus window — the union of the event tables and the positions —
   but a vessel can only be animated where there are AIS positions to
   interpolate. On the laptop corpus the real GFW tables tail back to 2012 while
   every position sits in the eight-week narrative at the far end, so 99% of the
   bar covered days on which nothing could move, and the default playhead (the
   window's end) sat past the last position entirely.

These tests cover the server halves: a cheap dedicated window endpoint that
reports the animatable span separately, and a seed list ranked by degree. The
client halves — request ordering, the session cache, the scrubber staying
mounted while disabled, and where the playhead parks — were verified in a real
browser and are not reachable from pytest.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from maritime_isr.api import graph_service as gsvc, service


def test_corpus_window_returns_the_spans_alone():
    w = service.get_corpus_window()
    assert set(w) == {"start", "end", "motion_start", "motion_end", "note"}


def test_corpus_window_agrees_with_the_one_stats_reports():
    """Two endpoints, one fact. If they drift, the scrubber and the dashboard
    would disagree about what window the operator is looking at.

    Only the corpus span is shared — `/stats` has no opinion about where the AIS
    positions sit, which is the extra thing the scrubber needs.
    """
    w = service.get_corpus_window()
    if not w["start"]:
        pytest.skip("no corpus landed")
    stats = service.get_stats()["corpus_window"]
    assert {k: w[k] for k in stats} == stats


def test_motion_window_is_the_ais_extent_not_the_corpus_extent():
    """The scrubber's window must come from `ais_position`, because that is the
    only table it can move a vessel with.

    The bug this pins: the corpus window is the union of the event tables and
    the positions, and on the laptop corpus the real GFW tail reaches back to
    2012 while every position sits in the eight-week narrative. Scrubbing the
    union spent 99% of the bar on days holding nothing that can move — the clock
    advanced and the map never changed.
    """
    w = service.get_corpus_window()
    if not w["motion_start"]:
        pytest.skip("no AIS positions landed")
    tracks = service.list_tracks(max_points=10_000)["items"]
    if not tracks:
        pytest.skip("no tracks to compare against")
    first = min(tr["points"][0][2] for tr in tracks)
    last = max(tr["points"][-1][2] for tr in tracks)
    lo = datetime.fromisoformat(w["motion_start"]).timestamp()
    hi = datetime.fromisoformat(w["motion_end"]).timestamp()
    # Tracks are decimated, so they can only sit INSIDE the window the endpoint
    # reports; what must not happen is a track falling outside it. The second of
    # slack is `list_tracks` truncating each timestamp to a whole second.
    assert lo - first <= 1 and last - hi <= 1, (
        f"tracks span {first}..{last}, outside the reported motion window "
        f"{lo}..{hi} — vessels would vanish at those clock positions")
    # And the window must be tight around the positions rather than the corpus:
    # a day of slack either side is decimation, a year is the old bug.
    assert (first - lo) < 86400 and (hi - last) < 86400


def test_corpus_window_says_so_when_it_plays_less_than_the_whole_corpus():
    """A control covering a different span from the map under it has to say so
    (CLAUDE.md §4: nothing asserted silently)."""
    w = service.get_corpus_window()
    if not w["start"] or not w["motion_start"]:
        pytest.skip("no corpus landed")
    corpus = (datetime.fromisoformat(w["end"])
              - datetime.fromisoformat(w["start"])).total_seconds()
    motion = (datetime.fromisoformat(w["motion_end"])
              - datetime.fromisoformat(w["motion_start"])).total_seconds()
    if corpus - motion < 86400:
        assert w["note"] is None      # nothing is being withheld
    else:
        assert w["note"] and w["start"][:10] in w["note"]


def test_the_note_names_both_spans_on_a_laptop_corpus():
    """The branch the sandbox corpus cannot reach.

    A scenario-only corpus has no real GFW tail, so its two windows coincide and
    the disclosure is correctly silent. These are the operator's real numbers
    (`data_profiles/real_corpus_profile.json`): a 5,317-day corpus carrying 52
    days of positions. The sentence has to name both.
    """
    note = service._window_note(
        {"start": "2012-01-04T06:18:14+00:00", "end": "2026-07-25T22:53:53+00:00"},
        {"motion_start": "2026-06-04T00:00:00+00:00",
         "motion_end": "2026-07-25T22:00:00+00:00"},
    )
    assert note is not None
    assert "2026-06-04" in note and "2012-01-04" in note
    assert "52 days" in note and "5317 days" in note


def test_the_note_says_it_plainly_when_there_is_no_ais_at_all():
    """The real-feed case (ADR-005): no free AIS, so nothing can ever move. The
    scrubber must not read as broken when it is merely empty."""
    note = service._window_note(
        {"start": "2012-01-04T06:18:14+00:00", "end": "2026-07-25T22:53:53+00:00"},
        {"motion_start": None, "motion_end": None},
    )
    assert note and "no AIS positions" in note


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
    # Guard on what MATCHED, not on the whole graph. The web is the ownership
    # subgraph now, so a corpus can hold thousands of nodes and still have
    # fewer than five carrying an ownership edge — in which case nothing is
    # truncated and there is nothing here to check.
    if g["matched_nodes"] <= 5:
        pytest.skip("fewer matching nodes than the test limit")
    assert g["truncated"] is True
    assert len(g["nodes"]) == 5
    assert g["matched_nodes"] > len(g["nodes"])
    assert g["matched_edges"] >= len(g["edges"])


def test_truncation_keeps_the_connected_core_not_an_arbitrary_slice():
    g = gsvc.full_graph(limit=5)
    if g["matched_nodes"] <= 5:
        pytest.skip("fewer matching nodes than the test limit")
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


# ---- one authority, however many node ids present as it --------------------
#
# The graph holds two sanctions-authority nodes that both display as "OFAC":
# the real one and the stand-in that carries generated designations, which an
# operator decision relabelled so the demo reads as one system. The graph then
# drew two identical OFAC diamonds with the designations split between them,
# which reads as two regulators. The merge is presentation only — the store
# keeps both ids and both keep their own is_synthetic flag.

def _auth(nid, label, degree, synthetic):
    return {"id": nid, "node_type": "sanctions_authority", "label": label,
            "degree": degree, "is_synthetic": synthetic}


def test_authorities_sharing_a_label_are_drawn_once():
    nodes = [_auth("authority:SCENARIO-SDN", "OFAC", 20, True),
             _auth("authority:OFAC", "OFAC", 5, False)]
    edges = [{"source": "v:1", "target": "authority:SCENARIO-SDN",
              "edge_type": "sanctioned-under", "t_start": "2024-01-01"}]
    out_nodes, out_edges = gsvc.merge_duplicate_authorities(nodes, edges)
    assert len(out_nodes) == 1
    # The REAL id survives, so clicking it shows the real regulator's props.
    assert out_nodes[0]["id"] == "authority:OFAC"
    # Degree carries across, so the focus pick sees one hub not two halves.
    assert out_nodes[0]["degree"] == 25
    # Generated if ANY part was — never the other way round.
    assert out_nodes[0]["is_synthetic"] is True
    assert out_edges[0]["target"] == "authority:OFAC"


def test_two_designations_onto_the_merged_node_become_one_edge():
    nodes = [_auth("authority:SCENARIO-SDN", "OFAC", 1, True),
             _auth("authority:OFAC", "OFAC", 1, False)]
    edges = [{"source": "v:1", "target": "authority:SCENARIO-SDN",
              "edge_type": "sanctioned-under", "t_start": "2024-01-01"},
             {"source": "v:1", "target": "authority:OFAC",
              "edge_type": "sanctioned-under", "t_start": "2024-01-01"}]
    _, out_edges = gsvc.merge_duplicate_authorities(nodes, edges)
    assert len(out_edges) == 1


def test_distinct_authorities_are_left_alone():
    nodes = [_auth("authority:OFAC", "OFAC", 3, False),
             _auth("authority:UN", "UN", 2, False)]
    out_nodes, _ = gsvc.merge_duplicate_authorities(nodes, [])
    assert len(out_nodes) == 2


def test_two_vessels_sharing_a_name_are_never_merged():
    """Merging on label alone would collapse two genuinely different hulls
    that happen to share a name — the opposite of what this product is for.
    Only authorities are folded, and only by (type, label)."""
    nodes = [{"id": "vessel:a", "node_type": "vessel", "label": "SEA STAR",
              "degree": 2, "is_synthetic": False},
             {"id": "vessel:b", "node_type": "vessel", "label": "SEA STAR",
              "degree": 2, "is_synthetic": False},
             _auth("authority:SCENARIO-SDN", "OFAC", 1, True),
             _auth("authority:OFAC", "OFAC", 1, False)]
    out_nodes, _ = gsvc.merge_duplicate_authorities(nodes, [])
    assert {n["id"] for n in out_nodes} == {
        "vessel:a", "vessel:b", "authority:OFAC"}
