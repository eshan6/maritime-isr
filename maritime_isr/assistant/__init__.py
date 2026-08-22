"""The MDA assistant — the ranked Vessel of Interest and the surface that
presents it.

Area 1 of the Section-3 build brief, and deliberately the first thing built:
it is the frame every other area plugs into. A capability built before the frame
exists is five disconnected features; a capability built after it visibly makes
the ranked list better, which is also the test that it was wired in at all.

What this package is
--------------------
* :mod:`.model` — the objects: Evidence, Factor, Recommendation,
  VesselOfInterest, Suppression.
* :mod:`.catalog` — the registry of factor kinds: weight, family, home area,
  candidate actions. One dict entry per kind of suspicion.
* :mod:`.score` — the composite, and the allocation that makes it decompose
  exactly to the factor.
* :mod:`.collect` — reading the graph and the landed tables into factors.
* :mod:`.narrate` — sentences a duty officer can read aloud on a radio call.
* :mod:`.recommend` — what to do next, tied to the factor that motivated it,
  with computed feasibility and stated capability.
* :mod:`.qa` — asking a question in ordinary language and getting a grounded
  answer, or an explicit "the system holds no record of that".
* :mod:`.build` — assembly, ranking, suppression and the workload measurement.

What this package is not
------------------------
**It does not detect anything.** Every factor here is assembled from a decision
some other module already made and calibrated. A collector that started
detecting would be a second, unmeasured copy of a rule that already exists, and
the first sign of it would be two numbers disagreeing in front of an operator.

**It never reads ground truth.** ``scenario_truth`` is the answer key and no
serving path may touch it (ADR-019 §d).
"""
from __future__ import annotations

from .build import MIN_SCORE, ask, build_list, build_one, workload
from .catalog import FACTOR_KINDS, FAMILIES, family_coverage
from .model import (Evidence, Factor, Recommendation, Suppression,
                    VesselOfInterest)
from .qa import Answer, GroundedQA, QuestionAnswerer, answerable_questions
from .score import combine, explain_arithmetic, score_factors, strength

__all__ = [
    "Evidence", "Factor", "Recommendation", "Suppression", "VesselOfInterest",
    "FACTOR_KINDS", "FAMILIES", "family_coverage",
    "score_factors", "explain_arithmetic", "combine", "strength",
    "Answer", "GroundedQA", "QuestionAnswerer", "answerable_questions",
    "build_list", "build_one", "workload", "ask", "MIN_SCORE",
]
