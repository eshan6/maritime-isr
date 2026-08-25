"""scenario_truth — what each scenario actually is, and what should happen.

**This table is ground truth and no detection, fusion, graph, scoring or
alerting code may read it.** A test greps those code paths and fails the build
if any of them imports this module or names the table. The reason is blunt: a
detector with access to the answer key measures nothing. Every precision and
recall figure this corpus produces is only worth the isolation of this file.

The measurement harness reads it. That is the one legitimate consumer, and it
runs *after* the pipeline has finished and produced its alerts.

**`expected_detection` is a claim about the system, and it can be wrong.** For a
true anomaly it says "we believe a correctly built system should fire here"; for
a decoy it says "a correctly built system should stay quiet". Those are design
intentions, and a scenario that goes undetected is tuning information rather
than a defect — the instruction for this session is to measure first and tune
separately, so a miss is recorded, not fixed.

For the deliberate misses `expected_detection` is **False on purpose**. The
sub-floor dhow is below Sentinel-1's reliable detection size and the offshore
gap is outside demonstrated reception, so firing on either would be the error.
Their value is that the *reason* for not firing is retrievable and renderable —
explaining a considered silence is a credibility moment, and a system that
cannot do it looks identical to one that simply missed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

TABLE = "scenario_truth"

# truth_class
TRUE_ANOMALY = "TRUE_ANOMALY"
DECOY = "DECOY"
DELIBERATE_MISS = "DELIBERATE_MISS"

TRUTH_CLASSES = (TRUE_ANOMALY, DECOY, DELIBERATE_MISS)

#: Families, for reporting precision and recall by group rather than only
#: overall — an aggregate number hides that one detector is carrying the score.
FAMILY_DARK_TRANSFER = "dark_transfer"
FAMILY_IDENTITY = "identity_manipulation"
FAMILY_SPOOFING = "spoofing"
FAMILY_GRAPH = "graph_ownership"
FAMILY_BEHAVIOURAL = "behavioural_geographic"
FAMILY_DECOY = "decoy"
FAMILY_BOUNDARY = "capability_boundary"
#: Area 4 (ADR-036): what the paperwork declares against what the track shows.
FAMILY_PAPERWORK = "paperwork"


@dataclass
class ScenarioTruth:
    """One row. Written by Layer 2 only."""
    scenario_id: str
    scenario_family: str
    truth_class: str
    entity_ids: list[str]
    t_start: datetime
    t_end: datetime
    expected_detection: bool
    #: Which detector we believe should fire (or would have to). Empty for
    #: decoys, where the expectation is that nothing fires at all.
    expected_anomaly_types: list[str] = field(default_factory=list)
    notes: str = ""
    #: Set where a scenario deliberately violates a physics rule the validator
    #: otherwise enforces (C3's impossible kinematics). The validator whitelists
    #: by scenario id, and only for the specific rule named here.
    physics_exemption: str = ""
    #: For DELIBERATE_MISS rows: the capability limit, with a number attached.
    capability_boundary: str = ""

    def __post_init__(self):
        if self.truth_class not in TRUTH_CLASSES:
            raise ValueError(f"truth_class must be one of {TRUTH_CLASSES}, "
                             f"got {self.truth_class!r}")
        if self.truth_class == DECOY and self.expected_detection:
            raise ValueError(
                f"{self.scenario_id}: a decoy that is expected to fire is not a "
                f"decoy. If it should fire, it is a TRUE_ANOMALY.")
        if self.truth_class == DELIBERATE_MISS and self.expected_detection:
            raise ValueError(
                f"{self.scenario_id}: a deliberate miss cannot expect detection")
        if self.truth_class == TRUE_ANOMALY and not self.expected_detection:
            # Legal, but it must be deliberate and explained — a true anomaly we
            # do not expect to catch is a recorded capability gap, not an
            # oversight, and the note is where that gets said.
            if not self.notes:
                raise ValueError(
                    f"{self.scenario_id}: a TRUE_ANOMALY with "
                    f"expected_detection=False needs a note saying why")
        if self.t_end < self.t_start:
            raise ValueError(f"{self.scenario_id}: time window runs backwards")
        if not self.entity_ids:
            raise ValueError(f"{self.scenario_id}: no entities involved")

    def as_row(self) -> dict:
        return dict(
            scenario_id=self.scenario_id,
            scenario_family=self.scenario_family,
            truth_class=self.truth_class,
            entity_ids=",".join(self.entity_ids),
            t_start=self.t_start,
            t_end=self.t_end,
            expected_detection=self.expected_detection,
            expected_anomaly_types=",".join(self.expected_anomaly_types),
            notes=self.notes,
            physics_exemption=self.physics_exemption,
            capability_boundary=self.capability_boundary,
        )


class TruthLedger:
    """Collects truth rows and refuses duplicates."""

    def __init__(self):
        self._rows: dict[str, ScenarioTruth] = {}

    def add(self, truth: ScenarioTruth) -> ScenarioTruth:
        if truth.scenario_id in self._rows:
            raise ValueError(f"duplicate scenario id {truth.scenario_id!r}")
        self._rows[truth.scenario_id] = truth
        return truth

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(sorted(self._rows.values(), key=lambda r: r.scenario_id))

    def get(self, scenario_id: str) -> ScenarioTruth | None:
        return self._rows.get(scenario_id)

    def by_class(self, truth_class: str) -> list[ScenarioTruth]:
        return [r for r in self if r.truth_class == truth_class]

    def by_family(self, family: str) -> list[ScenarioTruth]:
        return [r for r in self if r.scenario_family == family]

    def families(self) -> list[str]:
        return sorted({r.scenario_family for r in self})

    def physics_exemptions(self) -> dict[str, str]:
        """scenario_id -> the one rule it is allowed to break."""
        return {r.scenario_id: r.physics_exemption for r in self
                if r.physics_exemption}

    def entities_of(self, scenario_id: str) -> list[str]:
        r = self._rows.get(scenario_id)
        return list(r.entity_ids) if r else []

    def rows(self) -> list[dict]:
        return [r.as_row() for r in self]

    def summary(self) -> dict:
        return dict(
            total=len(self._rows),
            true_anomalies=len(self.by_class(TRUE_ANOMALY)),
            decoys=len(self.by_class(DECOY)),
            deliberate_misses=len(self.by_class(DELIBERATE_MISS)),
            expected_to_fire=sum(1 for r in self if r.expected_detection),
        )
