"""The composite suspicion score, and the decomposition that makes it usable.

The Section-3 brief is blunt about the requirement: *"Ranking is by a composite
score that must decompose — the score is worthless to a watchkeeper unless every
point of it traces to a named factor with evidence behind it."*

So the arithmetic here is chosen for one property above all others: **the parts
sum exactly to the whole.** Not approximately, not "the biggest contributor is",
exactly. An operator who reads "0.81, of which 0.42 is the sanctions
designation and 0.39 the dark contact" can argue with the system. One who reads
"0.81, driven mainly by sanctions" cannot.

How it works
------------
Each factor contributes an independent piece of evidence of strength
``s = weight x confidence``, clipped below 1. Treating the factors as
independent, the probability that *at least one* of them is telling the truth is
the noisy-OR::

    score = 1 - PROD(1 - s_k)

which is the right shape for suspicion: two moderate signals are worse than one,
piling up weak ones never reaches certainty, and nothing can exceed 1.

Noisy-OR does not decompose additively — but its logarithm does, exactly::

    -ln(1 - score) = SUM -ln(1 - s_k)                    ... call each term e_k

so ``e_k`` is a genuinely additive measure of how much evidence factor k
contributed. Allocating the final score back in proportion to ``e_k`` gives::

    points_k = score * e_k / SUM(e_j)

and ``SUM(points_k) == score`` by construction, to floating-point. That
identity is asserted in the tests, because it is the entire claim of this
module.

Three properties worth stating, because each was a design choice:

* **Order-independent.** The allocation depends only on the set of factors, not
  the order they were collected in. Two runs that find the same evidence produce
  the same number.
* **Monotone.** Adding a factor never lowers the score, and never raises another
  factor's *standalone* value — only its share of a larger total.
* **No floor and no ceiling games.** A single factor at weight 0.95 and
  confidence 1.0 scores 0.95, not 1.0. The system does not get to be certain.

Why not the existing ``anomaly.risk.risk_score``
------------------------------------------------
It stays, and it stays the vessel-level risk index the graph views use. It
decomposes into four *components* (anomaly history, sanction proximity, flag
opacity, fingerprint deviation), which is the right granularity for a risk board
and the wrong one for a watchkeeper: "anomaly_history 0.31" is not a reason, and
it cannot carry evidence, because several unrelated alerts are already summed
inside it. This module decomposes to the individual factor, which is the level
an operator reads aloud on a radio call. The two agree on direction and are
deliberately not the same number; :func:`compare_to_risk_index` exists so that
disagreement can be inspected rather than discovered.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from .catalog import weight_of
from .model import Factor

__all__ = ["strength", "combine", "score_factors", "explain_arithmetic",
           "MAX_SINGLE_STRENGTH"]

#: Nothing may be perfectly certain. A single factor at weight 1.0 and
#: confidence 1.0 would drive ``-ln(1-s)`` to infinity and make every other
#: factor's share exactly zero — the score would stop decomposing at precisely
#: the moment the evidence was strongest, which is backwards. The clip is a
#: statement about epistemics, not a numerical guard: this system does not
#: assert certainty about a vessel from remote sensing and a registry.
MAX_SINGLE_STRENGTH = 0.99


def strength(kind: str, confidence: float) -> float:
    """The evidential strength of one factor, in [0, MAX_SINGLE_STRENGTH]."""
    s = float(weight_of(kind)) * max(0.0, min(1.0, float(confidence)))
    return min(s, MAX_SINGLE_STRENGTH)


def combine(strengths: Iterable[float]) -> float:
    """Noisy-OR over independent evidence."""
    acc = 1.0
    for s in strengths:
        acc *= (1.0 - min(float(s), MAX_SINGLE_STRENGTH))
    return 1.0 - acc


def score_factors(factors: Sequence[Factor]) -> float:
    """Score a subject and write the decomposition back onto its factors.

    Mutates ``points``, ``share`` and ``standalone`` on each factor and returns
    the composite. Returns 0.0 for an empty set without touching anything.

    The write-back is deliberate: the allocation is only meaningful relative to
    the set it was computed over, so carrying it on the factor keeps it attached
    to its context. A factor lifted out of one subject and dropped into another
    must be re-scored, and a caller that forgets will see ``points`` that no
    longer sum — which the tests check for.
    """
    if not factors:
        return 0.0

    strengths = [strength(f.kind, f.confidence) for f in factors]
    total = combine(strengths)

    # Evidence weight in log space. `-log1p(-s)` is `-ln(1-s)` computed without
    # cancellation for small s, which matters here: a weight-0.25 factor at
    # confidence 0.1 has s = 0.025 and the naive form loses precision exactly
    # where the weakest factors live.
    logs = [-math.log1p(-s) for s in strengths]
    denom = sum(logs)

    for f, s, e in zip(factors, strengths, logs):
        f.weight = weight_of(f.kind)
        f.standalone = s
        if denom <= 0.0:
            # Every factor had zero strength — possible only if a confidence is
            # exactly 0. Allocate nothing rather than dividing by zero.
            f.points, f.share = 0.0, 0.0
        else:
            f.share = e / denom
            f.points = total * f.share
    return total


def explain_arithmetic(factors: Sequence[Factor], score: float) -> dict:
    """The sum, written out, so the number can be checked by hand.

    Returned alongside every VOI detail response. It is not decoration: the one
    question a sceptical operator asks first is "where did 0.81 come from", and
    an answer they can add up on paper is worth more than any amount of prose
    about explainability.
    """
    rows = []
    for f in factors:
        rows.append({
            "factor_id": f.factor_id,
            "kind": f.kind,
            "label": f.headline,
            "weight": round(float(f.weight), 4),
            "confidence": round(float(f.confidence), 4),
            "standalone": round(float(f.standalone or 0.0), 4),
            "points": round(float(f.points or 0.0), 4),
            "share_pct": round(100.0 * float(f.share or 0.0), 1),
        })
    rows.sort(key=lambda r: r["points"], reverse=True)
    allocated = sum(r["points"] for r in rows)
    return {
        "method": "noisy-OR over independent factors, allocated in log space",
        "formula": "score = 1 - PROD(1 - weight x confidence); "
                   "points_k = score x ln(1-s_k) / SUM ln(1-s_j)",
        "score": round(float(score), 4),
        "sum_of_points": round(allocated, 4),
        "reconciles": abs(allocated - float(score)) < 5e-4,
        "rows": rows,
    }


def compare_to_risk_index(store, subject_id: str, score: float,
                          at: float | None = None) -> dict | None:
    """This score beside ``anomaly.risk.risk_score``, for the same subject.

    Two numbers that measure related things differently will eventually be found
    to disagree, and it is far better that the product says so than that an
    operator discovers it. Returns None when the subject is not a graph vessel —
    the risk index only ranks hulls, and most subjects here are contacts.
    """
    from ..anomaly.risk import risk_score

    node = store.node(subject_id)
    if node is None or node.get("node_type") != "vessel":
        return None
    rs = risk_score(store, subject_id, at=at)
    return {
        "voi_score": round(float(score), 4),
        "risk_index": rs["risk_score"],
        "risk_components": rs["components"],
        "note": ("The two are deliberately different objects: the risk index "
                 "decomposes into four weighted components for a risk board, "
                 "this score decomposes into individual factors with evidence "
                 "for a watchkeeper. They agree on direction, not on value."),
    }
