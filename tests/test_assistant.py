"""Area 1 — the MDA assistant. ADR-031.

**Every test here exercises a code path.** A test that asserts a module imports
or a file exists is worthless (STATE.md, the six host-only bugs), so each of
these either runs the arithmetic and checks the identity it claims, or drives
the assembly over a real corpus and asserts something an operator would notice
if it broke.

The corpus-dependent tests skip themselves when no corpus is landed, and they
say so loudly — a bare checkout reporting green with the valuable half never
executed is exactly the trap STATE.md warns about. Generate one with::

    python -m maritime_isr.cli scenario generate --seed 7
    rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
"""
from __future__ import annotations

import math

import pytest

from maritime_isr import assistant as A
from maritime_isr.api import graph_service as gsvc
from maritime_isr.assistant import catalog, collect, qa, recommend, score
from maritime_isr.assistant.model import Evidence, Factor
from maritime_isr.config import ANOMALY_THRESHOLDS


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _ev(conf: float = 0.8, synthetic: bool = False) -> Evidence:
    return Evidence(kind="track_segment", label="a thing that happened",
                    confidence=conf, is_synthetic=synthetic,
                    provenance={"source_id": "test", "source_ref": "unit"})


def _factor(kind: str, conf: float, subject: str = "vessel:gfw:x",
            occurred_at: str | None = None) -> Factor:
    s = catalog.spec(kind)
    return Factor(kind=kind, subject_id=subject, headline=s.label,
                  confidence=conf, evidence=[_ev(conf)], family=s.family,
                  area=s.area, weight=s.weight, occurred_at=occurred_at)


@pytest.fixture(scope="module")
def listing():
    if not gsvc.graph_exists():
        pytest.skip("no object graph landed — run tools/run_scenario_pipeline.py")
    res = A.build_list(limit=1000)
    if not res["items"]:
        pytest.skip("graph holds no subject with any factor")
    return res


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

def test_every_detector_has_a_factor_kind():
    """A detector with no catalog entry scores nothing and narrates nothing.

    This is the regression test for a defect that shipped in the first build of
    this package: the catalog registered `dark_contact` while the detector
    raises `dark_vessel`, so **every dark-vessel alert in the corpus was
    silently dropped from the ranked list** — the headline capability of the
    whole system, absent from its own queue, with no error anywhere.
    """
    missing = sorted(set(ANOMALY_THRESHOLDS) - set(catalog.FACTOR_KINDS))
    assert not missing, (
        f"detector(s) {missing} have a precision gate in config.ANOMALY_"
        f"THRESHOLDS but no entry in assistant/catalog.py. Alerts they raise "
        f"will be dropped from the ranked list without an error.")


def test_every_factor_kind_narrates_and_recommends():
    """No registered kind may be scoreable but unexplainable."""
    for kind, s in catalog.FACTOR_KINDS.items():
        f = _factor(kind, 0.7)
        score.score_factors([f])
        from maritime_isr.assistant.narrate import narrate_factor
        text = narrate_factor(f, name="TEST HULL")
        assert len(text) > 30, f"{kind} narrates to nothing useful: {text!r}"
        assert "{" not in text and "None" not in text, \
            f"{kind} leaked a template or a null into its sentence: {text!r}"
        # A kind with no branch of its own falls through to the generic
        # fallback, which is true but dull — and that is exactly what happened
        # when `dark_contact` was renamed to `dark_vessel` and its sentence was
        # left behind on the old key. The fallback is a safety net, not an
        # acceptable resting place for a registered kind.
        generic = f"{s.label.lower()} — {s.blurb}"
        assert generic not in text.lower(), (
            f"{kind} has no narration branch of its own — it is falling "
            f"through to the generic fallback in narrate_factor()")
        assert s.actions, f"{kind} proposes no action"
        for action in s.actions:
            assert action in recommend.ACTIONS, \
                f"{kind} names unknown action {action!r}"


def test_every_action_states_its_capability_honestly():
    """CLAUDE.md §5: never imply the system can do something it cannot."""
    for name, meta in recommend.ACTIONS.items():
        assert meta["capability"], f"action {name} states no capability"
        assert meta["performed_by"] in ("operator", "system")


# --------------------------------------------------------------------------
# the score — the module's whole claim is that the parts sum to the whole
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kinds", [
    ["dark_vessel"],
    ["dark_vessel", "flag_opacity"],
    ["sanctions_designation", "dark_rendezvous", "identity_change",
     "port_risk_propagation"],
    ["ais_spoofing"] * 3,
])
def test_points_sum_exactly_to_the_score(kinds):
    factors = [_factor(k, 0.3 + 0.15 * i) for i, k in enumerate(kinds)]
    total = score.score_factors(factors)
    allocated = sum(f.points for f in factors)
    assert allocated == pytest.approx(total, abs=1e-9), (
        "the decomposition must sum to the score — that identity is the only "
        "reason this score is usable by a watchkeeper")
    assert sum(f.share for f in factors) == pytest.approx(1.0, abs=1e-9)


def test_score_is_bounded_and_never_certain():
    """A pile of strong factors approaches 1 but never reaches it."""
    factors = [_factor(k, 1.0) for k in
               ("sanctions_designation", "transponder_shutdown",
                "identity_then_anomaly", "dark_vessel", "ais_spoofing")]
    total = score.score_factors(factors)
    assert 0.0 < total < 1.0
    assert total > 0.99, "five maximal factors should be near-certain"


def test_score_is_monotone_in_added_evidence():
    base = [_factor("flag_opacity", 0.6)]
    more = [_factor("flag_opacity", 0.6), _factor("dark_vessel", 0.7)]
    assert score.score_factors(more) > score.score_factors(base)


def test_score_is_order_independent():
    a = [_factor("dark_vessel", 0.7), _factor("flag_opacity", 0.4),
         _factor("ais_spoofing", 0.55)]
    b = [a[2], a[0], a[1]]
    # Fresh objects, because score_factors mutates in place.
    fa = [_factor(f.kind, f.confidence) for f in a]
    fb = [_factor(f.kind, f.confidence) for f in b]
    assert score.score_factors(fa) == pytest.approx(score.score_factors(fb))


def test_single_factor_score_is_weight_times_confidence():
    """The simplest case must be inspectable by hand."""
    f = _factor("dark_vessel", 0.5)
    total = score.score_factors([f])
    assert total == pytest.approx(catalog.weight_of("dark_vessel") * 0.5)


def test_explain_arithmetic_reconciles():
    factors = [_factor("dark_vessel", 0.6), _factor("flag_opacity", 0.5),
               _factor("identity_change", 0.4)]
    total = score.score_factors(factors)
    ex = score.explain_arithmetic(factors, total)
    assert ex["reconciles"] is True
    assert ex["sum_of_points"] == pytest.approx(ex["score"], abs=5e-4)


def test_zero_confidence_contributes_nothing():
    factors = [_factor("dark_vessel", 0.0), _factor("flag_opacity", 0.6)]
    total = score.score_factors(factors)
    assert factors[0].points == pytest.approx(0.0, abs=1e-9)
    assert total == pytest.approx(catalog.weight_of("flag_opacity") * 0.6)


def test_no_infinity_from_a_maximal_factor():
    """MAX_SINGLE_STRENGTH exists so allocation never degenerates."""
    factors = [_factor("sanctions_designation", 1.0),
               _factor("flag_opacity", 0.5)]
    total = score.score_factors(factors)
    assert math.isfinite(total)
    assert all(math.isfinite(f.points) for f in factors)
    assert factors[1].points > 0.0, (
        "a maximal factor must not drive every other share to zero — the "
        "score would stop decomposing exactly when the evidence is strongest")


# --------------------------------------------------------------------------
# the model's refusals
# --------------------------------------------------------------------------

def test_a_factor_without_evidence_is_refused():
    with pytest.raises(ValueError, match="no evidence"):
        Factor(kind="dark_vessel", subject_id="vessel:gfw:x",
               headline="x", confidence=0.5, evidence=[])


def test_confidence_outside_the_unit_interval_is_refused():
    with pytest.raises(ValueError, match="outside"):
        Factor(kind="dark_vessel", subject_id="vessel:gfw:x", headline="x",
               confidence=1.4, evidence=[_ev()])


def test_synthetic_propagates_from_evidence_to_factor():
    f = Factor(kind="dark_vessel", subject_id="vessel:gfw:x", headline="x",
               confidence=0.5, evidence=[_ev(synthetic=True)])
    assert f.is_synthetic, (
        "ADR-019: a factor built on scenario evidence is scenario data, and "
        "the flag has to reach the surface, not just the database")


def test_unregistered_kind_raises_with_a_useful_message():
    with pytest.raises(KeyError, match="unregistered factor kind"):
        catalog.spec("no_such_kind")


# --------------------------------------------------------------------------
# merging — the workload reduction, made arithmetic
# --------------------------------------------------------------------------

def test_repeated_occurrences_merge_into_one_factor_and_strengthen_it():
    fs = [_factor("dark_rendezvous", 0.5, occurred_at=f"2026-06-0{i}")
          for i in (1, 2, 3)]
    merged = collect.merge_factors(fs)
    assert len(merged) == 1
    assert merged[0].detail["occurrences"] == 3
    assert len(merged[0].evidence) == 3
    assert merged[0].confidence > 0.5, "three occurrences beat one"
    assert merged[0].confidence < 1.0


def test_a_restated_standing_fact_does_not_accumulate():
    """The defect this semantics was introduced to fix.

    A designation arriving from the landed match table and again from the graph
    ownership walk is one fact seen twice. Combining it as two independent
    observations took 19 hulls to 0.97 confidence on the first build.
    """
    fs = [_factor("sanctioned_ownership", 0.8),
          _factor("sanctioned_ownership", 0.7)]
    merged = collect.merge_factors(fs)
    assert len(merged) == 1
    assert merged[0].confidence == pytest.approx(0.8), (
        "restating a standing fact corroborates it; it does not make it more "
        "true than its best single derivation")
    assert len(merged[0].evidence) == 2, "both derivations stay as evidence"


def test_merge_ids_are_stable_across_runs():
    def build():
        return collect.merge_factors(
            [_factor("dark_rendezvous", c, occurred_at="2026-06-01")
             for c in (0.4, 0.9, 0.6)])[0].factor_id
    assert build() == build(), (
        "a factor id travels into the UI and into an exported report; it must "
        "not depend on collection order")


def test_merge_keeps_distinct_kinds_apart():
    fs = [_factor("dark_vessel", 0.6), _factor("flag_opacity", 0.6)]
    assert len(collect.merge_factors(fs)) == 2


def test_merge_keeps_distinct_subjects_apart():
    fs = [_factor("dark_vessel", 0.6, subject="vessel:gfw:a"),
          _factor("dark_vessel", 0.6, subject="vessel:gfw:b")]
    assert len(collect.merge_factors(fs)) == 2


# --------------------------------------------------------------------------
# recommendations
# --------------------------------------------------------------------------

def test_feasibility_is_computed_from_real_geometry():
    """"Call her on VHF" is not advice if she is 300 km offshore."""
    near = recommend.recommend([_factor("transponder_shutdown", 0.8)],
                               position={"lat": 18.95, "lon": 72.90},
                               subject_kind="contact", name="X")
    far = recommend.recommend([_factor("transponder_shutdown", 0.8)],
                              position={"lat": 10.0, "lon": 63.0},
                              subject_kind="contact", name="X")
    vhf_near = next(r for r in near if r.action == "call_vhf")
    vhf_far = next(r for r in far if r.action == "call_vhf")
    assert vhf_near.feasible and "inside" in vhf_near.feasibility
    assert not vhf_far.feasible and "beyond" in vhf_far.feasibility
    assert vhf_far.priority < vhf_near.priority, \
        "an unavailable action must sink, but stay visible with its reason"


def test_recommendations_are_tied_to_the_factors_that_caused_them():
    fs = [_factor("transponder_shutdown", 0.8), _factor("flag_opacity", 0.4)]
    score.score_factors(fs)
    recs = recommend.recommend(fs, position={"lat": 18.95, "lon": 72.90},
                               subject_kind="vessel", name="X")
    ids = {f.factor_id for f in fs}
    assert recs, "some action should be proposed"
    for r in recs:
        assert r.because_factors, f"{r.action} names no motivating factor"
        assert set(r.because_factors) <= ids


def test_no_position_makes_range_limited_actions_infeasible():
    recs = recommend.recommend([_factor("dark_vessel", 0.7)], position={},
                               subject_kind="contact", name="X")
    cam = next(r for r in recs if r.action == "cue_eo_camera")
    assert not cam.feasible and "position" in cam.feasibility.lower()


# --------------------------------------------------------------------------
# the question answerer — its refusals matter more than its answers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,intent", [
    ("Why is she flagged?", "why_flagged"),
    ("What should I do about her?", "what_to_do"),
    ("Is she sanctioned?", "sanctions"),
    ("Who owns her?", "ownership"),
    ("What is her MMSI?", "identity"),
    ("Where is she now?", "position"),
    ("Has she gone dark before?", "dark_history"),
    ("Was a satellite overhead?", "imagery_opportunity"),
    ("How confident are you?", "confidence"),
    ("Is this real data?", "provenance"),
    ("Show me the evidence", "evidence"),
    ("How was the score calculated?", "score"),
])
def test_questions_route_to_the_right_intent(question, intent):
    got, unsupported, _ = qa.classify(question)
    assert unsupported is None
    assert got == intent


@pytest.mark.parametrize("question", [
    "Do you like pineapple on pizza?",
    "tell me a joke",
    "what is the capital of France",
    "",
])
def test_off_topic_questions_are_refused_not_answered(question):
    """A system that answers anything has an "I don't know" that means nothing.

    Regression for a measured defect: "Do you like pineapple on pizza?" matched
    `what_to_do` on the word "do" and returned a confident tasking list.
    """
    intent, unsupported, _ = qa.classify(question)
    assert intent is None and unsupported is None, (
        f"{question!r} should not match any intent")


@pytest.mark.parametrize("question,must_mention", [
    ("What cargo is she carrying?", "Area 4"),
    ("Who is the captain?", "Area 4"),
    ("Where will she go next?", "Area 2"),
    ("What did she say on the radio?", "Area 6"),
    ("What does she look like?", "Area 5"),
    ("Is she smuggling?", "intent"),
])
def test_unheld_topics_are_refused_and_say_which_area_would_hold_them(
        question, must_mention):
    intent, unsupported, _ = qa.classify(question)
    assert intent is None
    assert unsupported, f"{question!r} should be an explicit refusal"
    assert must_mention.lower() in unsupported.lower()


def test_the_answerer_never_invents_a_fact(listing):
    """Every sentence must be traceable to a retrieved row.

    Checked structurally rather than by reading prose: an `answered` outcome
    must name what it read, and a `no_data` outcome must phrase itself as a
    statement about the record rather than about the vessel.
    """
    sid = listing["items"][0]["subject_id"]
    for question in A.answerable_questions():
        a = A.ask(sid, question)
        assert a is not None
        assert a["outcome"] in ("answered", "no_data", "unsupported")
        if a["outcome"] == "answered":
            assert a["basis"], f"{question!r} answered but named no source"
        if a["outcome"] == "no_data":
            assert "holds no record" in a["text"]
            assert "statement about what has been ingested" in a["text"]


def test_an_unanswerable_question_lists_what_can_be_asked(listing):
    sid = listing["items"][0]["subject_id"]
    a = A.ask(sid, "do you like pineapple on pizza")
    assert a["outcome"] == "unsupported"
    assert a["grounded"] is False
    assert a["suggestions"], "a refusal must point at what is answerable"


def test_ask_on_an_unknown_subject_returns_none():
    assert A.ask("vessel:gfw:definitely-not-here", "why?") is None


# --------------------------------------------------------------------------
# assembly over the landed corpus
# --------------------------------------------------------------------------

def test_the_list_is_ranked_and_every_row_carries_its_reasons(listing):
    items = listing["items"]
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True), "the list must be ranked"
    for i, item in enumerate(items, 1):
        assert item["rank"] == i
        assert item["factors"], f"{item['subject_id']} is ranked with no factor"
        assert item["account"], "every row needs a plain-language account"
        assert item["recommendations"], "every row needs a proposed next step"
        allocated = sum(f["points"] for f in item["factors"])
        assert allocated == pytest.approx(item["score"], abs=1e-3), (
            f"{item['subject_id']}: factor points do not sum to its score")


def test_every_row_reaches_the_attention_threshold(listing):
    for item in listing["items"]:
        assert item["score"] >= listing["min_score"]


def test_suppressed_subjects_carry_a_stated_reason(listing):
    for s in listing["suppressed"]:
        assert s["reason"] and s["explanation"], (
            "'why is this NOT flagged' has to be answerable from the product")


def test_synthetic_rows_are_marked_on_the_surface(listing):
    for item in listing["items"]:
        if item["is_synthetic"]:
            assert "SCENARIO DATA" in item["account"], (
                "ADR-019 and the Section-3 standing caution: the synthetic "
                "flag stays visible in the interface, not merely in the "
                "database")


def test_counts_are_split_never_blended(listing):
    c = listing["count"]
    assert set(c) == {"real", "synthetic"}


def test_unbuilt_families_are_declared_rather_than_omitted(listing):
    families = {f["family"]: f for f in listing["coverage"]}
    for name in ("paperwork", "imagery", "radio"):
        assert name in families, (
            "an unbuilt evidence family must appear as absent; a surface that "
            "lists only what it found reads as complete")
        assert families[name]["areas"]


def test_the_detail_view_carries_evidence_and_the_arithmetic(listing):
    sid = listing["items"][0]["subject_id"]
    v = A.build_one(sid)
    assert v is not None
    assert v["arithmetic"]["reconciles"] is True
    assert v["not_known"], "the page must say what it does not know"
    for f in v["factors"]:
        assert f["evidence"], f"{f['kind']} reached the detail view bare"
        for e in f["evidence"]:
            assert e["provenance"].get("source_id"), (
                "CLAUDE.md §4.1: every evidence item names its source")


def test_build_one_accepts_a_bare_native_vessel_id(listing):
    vessels = [i for i in listing["items"]
               if i["subject_kind"] == "vessel"]
    if not vessels:
        pytest.skip("no hull-subject on this corpus")
    canonical = vessels[0]["subject_id"]
    native = canonical.split(":", 2)[-1]
    assert A.build_one(native) is not None, (
        "a link pasted from another screen may carry either spelling")


def test_build_one_on_an_unknown_subject_returns_none():
    assert A.build_one("vessel:gfw:definitely-not-here") is None


def test_workload_ratio_is_measured_and_labelled(listing):
    w = A.workload()
    assert w["inputs"]["total_tracks"] > 0
    assert w["outputs"]["subjects_on_the_list"] > 0
    assert w["inputs"]["total_tracks"] > w["outputs"]["subjects_on_the_list"], (
        "if the queue is not shorter than the picture there is no claim to make")
    assert w["ratios"]["fraction_of_tracks_surfaced"] < 1.0
    assert "synthetic" in w["corpus"] or "real" in w["corpus"]
    assert w["caveat"], "no ratio leaves this system without its caveat"


def test_the_assistant_is_covered_by_the_truth_isolation_guard():
    """ADR-019 §d: no serving path may touch the answer key.

    The check itself lives in ``test_scenario.py``, which already parses rather
    than greps — it distinguishes a module that *documents* the rule from one
    that reads the table, and it has its own negative control. Duplicating it
    here would give a second, weaker copy that flags docstrings; what this test
    guards instead is that ``assistant`` is enrolled in the real one at all,
    which is the way a package silently escapes an isolation rule.
    """
    from tests.test_scenario import DETECTION_PATHS, _truth_references

    assert "assistant" in DETECTION_PATHS, (
        "the assistant package is not covered by the ground-truth isolation "
        "check — add it to DETECTION_PATHS in tests/test_scenario.py")
    import pathlib
    offenders = [
        f"{p.name}:{hit}"
        for p in pathlib.Path(A.__file__).parent.rglob("*.py")
        for hit in _truth_references(p)]
    assert not offenders, f"assistant reads ground truth: {offenders}"
