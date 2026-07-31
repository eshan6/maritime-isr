"""Scenario generation — synthetic maritime behaviour in the real tables.

Two layers, strictly separated:

  **Layer 1, `scenario.primitives`** — vessels, tracks, AIS emission,
  rendezvous, gaps, port calls, identity events, corporate structure. Physics
  and geometry live here, and nothing here knows what a scenario *means*.

  **Layer 2, `scenario.scenarios`** — compositions of primitives plus exactly
  one `scenario_truth` row each. Meaning lives here and nowhere else.

The separation is what makes the measurement worth anything: a true positive and
a decoy are built by the same primitive calls with the same fidelity, so a
detector that tells them apart must be doing it on behaviour rather than on a
code artefact.

Everything lands in the tables real data lands in, flagged `is_synthetic` and
sourced `synthetic-scenario` — see ADR-019 and `land.py`. `scenario_truth` is
the exception and is the one table detection code may never read.
"""
from .run import (GenerationResult, clear, format_generation, format_status,
                  generate, landed_counts, status)
from .truth import (DECOY, DELIBERATE_MISS, TRUE_ANOMALY, ScenarioTruth,
                    TruthLedger)
from .validate import ValidationReport, validate_world
from .world import T0, T1, ScenarioWorld, week

__all__ = [
    "GenerationResult", "clear", "format_generation", "format_status",
    "generate", "landed_counts", "status",
    "DECOY", "DELIBERATE_MISS", "TRUE_ANOMALY", "ScenarioTruth", "TruthLedger",
    "ValidationReport", "validate_world",
    "T0", "T1", "ScenarioWorld", "week",
]
