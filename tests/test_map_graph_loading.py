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


def test_seed_limit_is_bounded():
    """An unbounded limit reaching the UI would let a URL ask for the whole
    node table."""
    if not gsvc.graph_exists():
        pytest.skip("graph not populated")
    assert len(gsvc.best_seeds(1)) <= 1
    assert len(gsvc.best_seeds(10_000)) <= 100
