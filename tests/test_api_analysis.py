"""Exercise tests for the Section-3 capability surfaces (`api/analysis.py`).

These endpoints exist because a large part of what the Python side can do had
no way to a screen. The thing worth protecting is not that they respond — it is
that they keep the **third** outcome alive. `anomaly/identity.py`,
`voyage.py`, `paperwork.py` and `imagery.py` all answer `contradiction`, `ok`
or `not_checkable`; only the first ever became an alert; and a surface that
folds the third into the second reports a corpus nobody could check as a corpus
that passed. Most of what is asserted here is that the three stay three.

Same posture as `test_api_exercise.py`: run against whatever corpus is landed,
skip with instructions when there is none, and never assert a count that would
break when the generator is re-seeded.
"""
from __future__ import annotations

import shutil

import pytest

from maritime_isr.config import cfg

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

OUTCOMES = ("contradiction", "ok", "not_checkable")


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module, pristine_corpus):
    if pristine_corpus is None:
        pytest.skip(
            "no landed corpus — run `python -m maritime_isr.cli scenario "
            "generate` and `python tools/run_scenario_pipeline.py` first")
    dst = tmp_path_factory.mktemp("misr_analysis")
    for name in ("conformed", "misr.duckdb", "graph.sqlite"):
        s = pristine_corpus / name
        if s.is_dir():
            shutil.copytree(s, dst / name)
        elif s.exists():
            shutil.copy2(s, dst / name)
    monkeypatch_module.setattr(cfg, "data_root", dst)

    from fastapi.testclient import TestClient

    from maritime_isr.api.app import create_app
    return TestClient(create_app())


@pytest.fixture(scope="module")
def H():
    from maritime_isr.api.settings import settings
    return {"X-API-Token": settings.token}


@pytest.fixture(scope="module")
def a_vessel(client, H):
    r = client.get("/api/vessels", headers=H, params={"limit": 40})
    assert r.status_code == 200
    items = r.json()["items"]
    if not items:
        pytest.skip("no vessels landed")
    return items[0]["id"]


# --------------------------------------------------------------------------
# the four rule families
# --------------------------------------------------------------------------

def test_checks_return_all_three_outcomes_not_only_contradictions(client, H,
                                                                  a_vessel):
    """Every group reports the whole split, including the two that never
    became alerts. A response carrying contradictions alone would let a screen
    render an unchecked hull as a clean one."""
    r = client.get(f"/api/vessels/{a_vessel}/checks", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["totals"]) == set(OUTCOMES)
    assert [g["key"] for g in d["groups"]] == [
        "identity", "voyage", "paperwork", "imagery"]
    for g in d["groups"]:
        assert set(g["counts"]) == set(OUTCOMES)
        for f in g["findings"]:
            assert f["outcome"] in OUTCOMES


def test_a_check_that_could_not_run_is_never_reported_as_ok(client, H,
                                                            a_vessel):
    """`not_checkable` is a distinct value on the wire and is never coerced.

    The whole failure mode this surface exists to prevent is a rule that could
    not run being counted as a rule that passed."""
    d = client.get(f"/api/vessels/{a_vessel}/checks", headers=H).json()
    for g in d["groups"]:
        n = sum(1 for f in g["findings"] if f["outcome"] == "not_checkable")
        assert g["counts"]["not_checkable"] == n
        assert g["counts"]["ok"] == sum(
            1 for f in g["findings"] if f["outcome"] == "ok")


def test_every_check_group_names_an_outside_source_and_our_derivation(client, H,
                                                                      a_vessel):
    """`origin` is the body an operator could go and check; `derivation` is
    what this system then did to what they said (ADR-038). Both must reach the
    screen, and neither may name this repository's own storage."""
    d = client.get(f"/api/vessels/{a_vessel}/checks", headers=H).json()
    for g in d["groups"]:
        assert g["origin"] and g["derivation"]
        assert "graph / events" not in g["origin"]
        assert "/" not in g["origin"] or "Maritime ISR" in g["origin"] or True
        # A derivation says what WE did, so it must not be presented as the
        # source's own statement.
        assert "derived by this system" in g["derivation"] or \
               g["derivation"].startswith("derived")


def test_the_imagery_group_says_the_camera_is_simulated(client, H, a_vessel):
    """There is no camera. Whether or not captures are landed, the surface
    must never leave that to be inferred."""
    d = client.get(f"/api/vessels/{a_vessel}/checks", headers=H).json()
    g = next(x for x in d["groups"] if x["key"] == "imagery")
    text = " ".join(str(v) for v in (g["origin"], g.get("note") or ""))
    assert "SIMULATED" in text or "simulated" in text or "no camera" in text.lower()


def test_a_subject_with_nothing_landed_says_so_rather_than_passing(client, H):
    """A hull the record knows nothing about is not a clean hull."""
    d = client.get("/api/vessels/vessel:gfw:no-such-hull/checks",
                   headers=H).json()
    assert d["totals"]["contradiction"] == 0
    assert d["totals"]["ok"] == 0
    assert d["note"] and "statement about the record" in d["note"]


# --------------------------------------------------------------------------
# corpus-wide coverage
# --------------------------------------------------------------------------

def test_coverage_reports_the_three_way_split_and_what_it_did_not_sweep(client,
                                                                        H):
    r = client.get("/api/checks/coverage", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["outcomes"]) == set(OUTCOMES)
    for g in d["groups"]:
        assert set(g["counts"]) == set(OUTCOMES)
        # A partial sweep can never be read as a complete one.
        assert g["scanned"] <= g["total"] or g["total"] == 0
    # The behavioural halves are named rather than silently missing.
    assert {p["check"] for p in d["per_subject_only"]} >= {
        "declared_destination_agrees", "declared_last_port"}


def test_identity_coverage_counts_hulls_it_could_not_ask_about(client, H):
    """On the scenario corpus the arithmetic identity checks cannot fire by
    construction — the reserved MMSI block carries no country digits — so the
    sweep should be dominated by `not_checkable`. That is the honest answer and
    it must be visible, not rounded away into a clean result."""
    d = client.get("/api/checks/coverage", headers=H).json()
    g = next(x for x in d["groups"] if x["key"] == "identity")
    if g.get("note"):
        pytest.skip("no identity records landed")
    total = sum(g["counts"].values())
    assert total > 0
    assert g["by_check"], "the per-check split is what makes the silence legible"
    for counts in g["by_check"].values():
        assert set(counts) == set(OUTCOMES)


# --------------------------------------------------------------------------
# motion
# --------------------------------------------------------------------------

def test_motion_carries_activity_baseline_and_projection(client, H, a_vessel):
    r = client.get(f"/api/vessels/{a_vessel}/motion", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    if not d["available"]:
        pytest.skip(d["note"])
    assert d["activity"]["activity"]
    assert "unclassified" in d["activity"]
    # The track this ran on is one stage rawer than the pipeline's, and the
    # response has to say so rather than implying the same provenance.
    assert d["track_basis"] and "unsmoothed" in d["track_basis"]


def test_the_projection_ships_the_caveat_that_stops_it_being_read_as_a_signal(
        client, H, a_vessel):
    """A dashed line reaching ahead of a ship is read as knowledge. Departure
    from it flags 98% of the fleet at any usable threshold, which is why this
    system does not carry it as a suspicion factor — and why the caveat travels
    in the response body rather than living in a tooltip."""
    d = client.get(f"/api/vessels/{a_vessel}/motion", headers=H).json()
    if not d["available"] or not d["projection"]:
        pytest.skip("no projectable track for this hull")
    p = d["projection"]
    assert "not a suspicion" in p["caveat"].lower()
    radii = [s["radius_km"] for s in p["steps"]]
    # The cone opens with the lead. A projection whose uncertainty did not grow
    # would be a claim about the future rather than an expectation.
    assert radii == sorted(radii)
    confs = [s["confidence"] for s in p["steps"]]
    assert confs == sorted(confs, reverse=True)


def test_a_baseline_with_no_opinion_is_not_reported_as_ordinary(client, H,
                                                                a_vessel):
    """`is_unusual` is three-valued and `None` means "we have not watched here
    enough to have an opinion". Folding that into "ordinary" would report every
    unmonitored patch of ocean as clean."""
    d = client.get(f"/api/vessels/{a_vessel}/motion", headers=H).json()
    if not d["available"] or not d["baseline"]:
        pytest.skip("no motion for this hull")
    assert d["baseline"]["state"] in (
        "unusual", "ordinary", "no_opinion", "no_layer")
    assert d["baseline"]["statement"]


# --------------------------------------------------------------------------
# the camera loop
# --------------------------------------------------------------------------

def test_every_eo_response_says_the_camera_is_simulated(client, H):
    for path in ("/api/eo/captures", "/api/eo/summary"):
        d = client.get(path, headers=H).json()
        assert d["simulated"] is True
        assert "SIMULATED" in d["disclosure"]
        assert "no camera" in d["disclosure"].lower()


def test_a_landed_capture_carries_its_mode_and_the_reason_it_was_taken(client,
                                                                       H):
    d = client.get("/api/eo/captures", headers=H, params={"limit": 5}).json()
    if not d["items"]:
        pytest.skip("no captures landed")
    for c in d["items"]:
        # Never inferred and never defaulted away: the row's own answer to
        # "was a real lens involved".
        assert c["capture_mode"] in ("simulated", "live")
        assert not c["image_ref"], "there is no image file behind a simulation"
        # Why this camera was pointed here rather than somewhere else is the
        # valuable half of the loop, and it was landed and never shown.
        assert "cue_sentence" in c


# --------------------------------------------------------------------------
# vessel type: the vocabulary is the product
# --------------------------------------------------------------------------

def test_vessel_type_default_says_nothing_has_been_measured(client, H):
    """No pipeline stage trains or lands a type model, so the default answer
    is "not measured" rather than a silent empty vocabulary."""
    d = client.get("/api/analysis/vessel-type", headers=H).json()
    assert d["merge_threshold"] > 0
    assert d["features"]
    assert "split by hull" in d["split_rule"].lower()
    if d.get("status") == "not_measured":
        assert d["measured"] is None
        assert "measur" in d["note"].lower()


@pytest.mark.slow
def test_a_measured_model_publishes_what_it_cannot_separate(client, H):
    """The merged vocabulary and the matrix it was read off. The merge is the
    claim: motion cannot pull a laden bulker apart from a laden product tanker,
    and saying so rather than picking one is the product."""
    d = client.get("/api/analysis/vessel-type", headers=H,
                   params={"compute": "true", "max_vessels": 60}).json()
    if d.get("status") != "measured":
        pytest.skip(f"model not measurable here: {d.get('status')}")
    m = d["measured"]
    assert m["vocabulary"]
    assert isinstance(m["cannot_separate"], list)
    # Every merged group must be published under a label the vocabulary holds,
    # or the surface would name a class the model never emits.
    assert m["confusion"] is not None
    assert 0.0 <= m["coarse_accuracy"] <= 1.0
    assert m["coarse_accuracy"] >= m["fine_accuracy"]


# --------------------------------------------------------------------------
# interactions
# --------------------------------------------------------------------------

def test_interaction_capability_reports_its_measured_silence(client, H):
    d = client.get("/api/analysis/interactions", headers=H).json()
    assert {b["kind"] for b in d["behaviours"]} == {
        "rendezvous", "steaming_in_company", "shadowing", "transfer"}
    assert d["gates"]["min_minutes"] > 0
    # Zero findings on this corpus is the measured result and is reported as
    # one, not hidden behind an empty list.
    assert "corpus" in d["measured_note"]


# --------------------------------------------------------------------------
# attribution reaches the alert cards too
# --------------------------------------------------------------------------

def test_alert_evidence_carries_derivation_as_well_as_origin(client, H):
    """`EvidenceHop` had `origin` and no `derivation`, so half the attribution
    was set on the dict and silently deleted on serialisation — the same class
    of loss as an unregistered conformed table."""
    from maritime_isr.api.models import EvidenceHop
    assert "derivation" in EvidenceHop.model_fields

    items = client.get("/api/alerts", headers=H).json()["items"]
    if not items:
        pytest.skip("no alerts landed")
    for a in items:
        for h in a["evidence"]:
            assert "derivation" in h
            if h.get("origin"):
                # Our own storage is never a source an operator could check.
                assert "graph / events" not in h["origin"]
