"""The wider fleet: is it on the map, does it match its labels, is it boring?

`test_scenario.py` checks that the corpus is *possible* — speeds, turn rates,
identifiers, land. These four checks are about whether it is *useful*, which is
a different question and one the corpus can fail while passing every physics
validator in the project.

The three properties, and why each is a test rather than a comment:

  **Every hull is somewhere.** A vessel minted into the registry, wired into the
  graph and never moved is invisible on the map, invisible to the radar
  simulator and invisible to the track engine, while still counting toward
  "674 vessels" in every report. That is a corpus that lies about its own size,
  and nothing else in the suite would notice.

  **Motion matches the label.** `tracks/vessel_type.py` infers a contact's class
  from motion alone and is measured against this corpus using the declared class
  as its label, so a hull labelled `fishing` that steams a straight line at
  thirteen knots moves a number the product quotes. This one is enforced twice —
  by `validate._check_archetypes` on every generate, and here — because the
  validator can be quietly weakened by widening a band, and a band widened to
  make the build pass is exactly the failure this catches.

  **The fleet is boring.** ADR-004 makes precision the binding constraint, and a
  precision figure measured on a corpus where a third of the hulls are guilty is
  a figure about the corpus. The ceiling is asserted so that adding scenarios
  later cannot drift the base rate upward one plausible commit at a time.

  **Nothing here touches the shared RNG.** Checked at source level, because it
  cannot be checked at runtime without a second corpus to compare against, and
  because the cost of getting it wrong is that every previously measured number
  in the project silently becomes incomparable (see `scenario/fleet.py`).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from maritime_isr.scenario import cast as cast_mod
from maritime_isr.scenario import fleet
from maritime_isr.scenario.truth import TRUE_ANOMALY


REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def world(build_world):
    return build_world(7)


def test_the_cast_report_reconciles(world):
    """`cohorts()` must add up to the hulls that actually exist.

    The generation report prints this sum in the operator's face on every run.
    It was out by four hundred while the wider fleet was being added, which is
    the state that teaches a reader to skim the one report the project relies on
    somebody actually checking.
    """
    assert cast_mod.expected_vessel_count() == len(world.vessels), (
        f"cohorts() sums to {cast_mod.expected_vessel_count()} but "
        f"{len(world.vessels)} hulls were minted — a group is missing from "
        f"`cast.cohorts()`, or a hull was minted outside one")


def test_every_fleet_hull_is_on_the_map(world):
    missing = [k for k in fleet.fleet_keys()
               if not world.tracks.get(f"vessel:{k}")]
    assert not missing, (
        f"{len(missing)} fleet hull(s) exist in the registry and nowhere in the "
        f"picture, e.g. {missing[:5]} — they inflate every vessel count while "
        f"being invisible to the map, the radar simulator and the track engine")


def test_every_fleet_hull_broadcasts(world):
    """A hull with a track and no AIS is a permanent dark vessel by accident."""
    silent = [k for k in fleet.fleet_keys()
              if world.vessels[f"vessel:{k}"].ais_expected
              and not world.ais.get(f"vessel:{k}")]
    assert not silent, (
        f"{len(silent)} fleet hull(s) are expected to transmit and emitted "
        f"nothing, e.g. {silent[:5]} — each is a dark vessel the corpus never "
        f"meant to stage, and the dark-vessel measurement counts it")


def test_motion_matches_the_declared_vessel_class(world):
    """Every bulk hull inside her own archetype's kinematic envelope."""
    bad = []
    for a in fleet.ARCHETYPES:
        for i in range(a.count):
            eid = f"vessel:{a.hull_key(i)}"
            pts = world.tracks.get(eid)
            if not pts:
                continue                      # the previous test owns that case
            m = fleet.measure_motion(world.track_of(eid))
            for problem in a.violations(m):
                bad.append(f"{a.hull_key(i)} ({a.vessel_class}): {problem}")
    assert not bad, (
        f"{len(bad)} hull(s) move unlike the class they are labelled with — "
        f"each one is a mislabelled example in the corpus the vessel-type "
        f"model is measured on:\n  " + "\n  ".join(bad[:10]))


def test_each_archetype_is_distinguishable_from_the_others(world):
    """Two archetypes whose bands are identical are one archetype twice over.

    Not a test of the generator so much as of the *design*: the point of adding
    container, tug, osv and ferry was that the corpus contained no hull that
    works inside a harbour and none that holds station beside a platform. If two
    trades end up describing the same envelope, the corpus has gained hulls and
    no information, and the classifier's confusion matrix will say so later at
    much greater expense.
    """
    seen: dict[tuple, str] = {}
    for a in fleet.ARCHETYPES:
        band = (a.sog_p50, a.sog_p90, a.turn_deg_min)
        assert band not in seen, (
            f"archetypes {seen[band]} and {a.key} declare identical kinematic "
            f"envelopes — one of them is not earning its place")
        seen[band] = a.key


def test_every_fleet_hull_has_a_time_scoped_graded_operator_edge(world):
    """CLAUDE.md invariant 3: no naked facts in the graph.

    Every edge carries provenance, a confidence and a validity interval. Checked
    on the fleet specifically because it is where the graph grew by four hundred
    hulls at once, and an unwired hull is not visibly broken — she simply never
    appears in an ownership traversal, and the answer to "who else does this
    company run" comes back quietly short.
    """
    by_src: dict[str, list] = {}
    for e in world.corporate.edges:
        by_src.setdefault(e.src, []).append(e)

    for key in fleet.fleet_keys():
        edges = [e for e in by_src.get(f"vessel:{key}", [])
                 if e.kind == "operated-by"]
        assert len(edges) == 1, (
            f"{key} has {len(edges)} operator edge(s); every hull has exactly "
            f"one operator at a time")
        e = edges[0]
        assert e.confidence is not None and 0.0 < e.confidence <= 1.0
        assert e.valid_from is not None, f"{key}'s operator edge has no start"


def test_the_fleet_is_overwhelmingly_boring(world):
    """The anomaly base rate, asserted as a ceiling rather than described.

    ADR-004 makes precision the binding constraint, and precision measured
    against a guilty corpus is a statement about the corpus.

    Measured on the landed corpus at seed 7: **60 of 674 hulls (8.9%)** carry a
    staged anomaly, down from **50 of 253 (19.8%)** before this fleet existed.
    All 394 bulk fleet hulls are boring; the ten new anomalous hulls are group
    W's, placed by hand, where they are outnumbered by their own decoys.

    The ceiling is asserted at 12% rather than at the measured 8.9% because the
    number this guards against is *drift*: a later group adding four anomalies
    and no ordinary traffic, then another, each individually reasonable. 12%
    leaves room for that to happen a few times and stops it before the
    denominator flatters every precision figure in the project again. A group
    that pushes past it should have to come here and argue for it.
    """
    flagged = {e for t in world.truth if t.truth_class == TRUE_ANOMALY
               for e in t.entity_ids}
    rate = len(flagged) / len(world.vessels)
    assert rate <= 0.12, (
        f"{len(flagged)} of {len(world.vessels)} hulls carry a staged anomaly "
        f"({rate:.1%}) — a precision figure measured here is flattered by the "
        f"denominator (ADR-004)")


def test_the_wider_fleet_adds_far_more_boring_hulls_than_interesting_ones(world):
    """The fleet must dilute the corpus, not concentrate it."""
    flagged = {e for t in world.truth if t.truth_class == TRUE_ANOMALY
               for e in t.entity_ids}
    fleet_flagged = {e for e in flagged
                     if e.replace("vessel:", "") in set(fleet.fleet_keys())}
    rate = len(fleet_flagged) / fleet.total_size()
    assert rate <= 0.05, (
        f"{len(fleet_flagged)} of {fleet.total_size()} wider-fleet hulls are "
        f"staged anomalies ({rate:.1%}); the fleet exists to be the sea the "
        f"scenarios happen in")


def test_no_fleet_code_draws_from_the_shared_random_stream():
    """`world.rng` is off limits to everything that touches a fleet hull.

    Adding a single Suezmax through the shared stream once re-rolled the whole
    background fleet behind it and moved the vessel-type model's measured coarse
    accuracy from above its 75% floor to 65% — a number that looked exactly like
    a regression and was not one. Four hundred hulls added the same way would
    have made every previously measured figure in this project incomparable in
    one commit, which is why this is a test and not a convention.

    **Parsed, not grepped**, and that distinction is the whole reliability of
    this check. A regex over the source lines cannot tell a use of `world.rng`
    from a sentence explaining why nobody may use it — and since the rule is
    important enough to be written down in three docstrings, grepping made the
    modules that document the rule best look like the modules that break it.
    The prose failure is the loud kind; the quiet kind is worse: a maintainer
    who has seen this test cry wolf once deletes the comment to make it pass and
    the next real violation lands unremarked. Walking the AST asks the only
    question that matters — is there an attribute load of `rng` off a name
    `world` in code that will actually execute — and docstrings and comments are
    not in the tree to be misread.
    """
    files = [REPO / "maritime_isr" / "scenario" / "fleet.py",
             REPO / "maritime_isr" / "scenario" / "scenarios" / "fleet_traffic.py",
             REPO / "maritime_isr" / "scenario" / "scenarios" / "group_w.py"]
    for f in files:
        tree = ast.parse(f.read_text(), filename=str(f))
        hits = sorted({
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "rng"
            and isinstance(node.value, ast.Name) and node.value.id == "world"})
        assert not hits, (
            f"{f.name} draws from the shared RNG at line(s) {hits} — use "
            f"`fleet.fleet_rng(world, salt=...)`, or every hull minted before "
            f"this module is re-rolled and every measured number moves")
