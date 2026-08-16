"""radar_dark_truth — which contacts really were dark, and for how long.

**Second quarantined table, same rule as `scenario_truth`: no detection, fusion,
graph, scoring or alerting code may read it.** `tests/test_scenario.py` enforces
that by parsing every module in those packages, and the check knows about this
table by name. A detector with the answer key measures nothing, and a radar
detector with the answer key measures nothing twice over — the whole claim being
tested is that unexplained radar tracks can be found *without* being told which
ones they are.

**Why a separate table rather than more rows in `scenario_truth`.** The two
answer different questions at different granularities. `scenario_truth` is one
row per *scenario*: a narrative, its participants, and whether the system should
have fired. This is one row per *episode*: a vessel, a window, a position, and
whether radar could physically have seen her. A dark-contact measurement needs
to attribute a produced contact to a place and a time, which a scenario-level
row cannot do — and forcing it to would have meant either forty rows per
scenario or a scenario id on something that is not a scenario.

**The episode is derived from the generated world, never authored.** A scenario
says "her transponder is off from Tuesday afternoon"; whether that produces a
dark *episode* depends on whether any station could see her, which depends on
where she actually sailed. So the ledger is computed after the picture is
generated, from the plots and the emitted AIS — which is also why it can record
the honest reason a true dark period produced no episode at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

TABLE = "radar_dark_truth"

# Why the vessel was not on AIS during the episode. Recorded so a miss can be
# explained: missing a never-fitted dhow is a different failure from missing a
# tanker that switched off mid-passage.
#
# The four are stated as facts we can observe about the corpus, not as motives.
# In particular `silent_throughout` and `transponder_off` are deliberately kept
# apart: the second is a hull we HEARD at other times and did not hear here,
# which is the strong case; the first is a hull no receiver ever heard at all,
# which is consistent with a shutdown and also with a voyage spent entirely
# outside reception, and pretending to know which would be inventing intent.
CAUSE_TRANSPONDER_OFF = "transponder_off"      # heard before/after, not here
CAUSE_NEVER_TRANSMITS = "never_transmits"      # no transponder fitted
CAUSE_SILENT_THROUGHOUT = "silent_throughout"  # fitted, never heard once
CAUSE_OUT_OF_RECEPTION = "out_of_reception"    # transmitting, nobody listening
CAUSES = (CAUSE_TRANSPONDER_OFF, CAUSE_NEVER_TRANSMITS,
          CAUSE_SILENT_THROUGHOUT, CAUSE_OUT_OF_RECEPTION)


@dataclass
class RadarDarkEpisode:
    """One interval in which radar saw a vessel and AIS did not explain her."""

    episode_id: str
    entity_id: str
    t_start: datetime
    t_end: datetime
    #: Representative position — the midpoint of the episode's plots.
    lat: float
    lon: float
    #: The bounding box of the episode's plots, which is what a produced contact
    #: is actually matched against.
    #:
    #: **A midpoint alone is not enough and the difference is large.** An episode
    #: is a vessel *under way*: R1's coaster covers 70 nm in her longest dark
    #: run, so a contact produced from the first hour of it sits 60 nm from the
    #: midpoint. Matching on a radius around the midpoint either has to be so
    #: generous that unrelated contacts fall inside it, or it scores correct
    #: detections as misses. The box is where she demonstrably was.
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    #: The hull's true length. The measurement uses it to say whether a miss was
    #: a detector failure or a stated size-floor boundary.
    length_m: float
    cause: str
    #: How many radar plots the episode rests on, and over how long. A one-plot
    #: episode is not something any honest detector should be expected to find,
    #: and separating those out is what stops recall being quietly deflated by
    #: episodes nobody could have caught.
    n_plots: int
    duration_min: float
    #: Stations that contributed. An episode seen by one station on the edge of
    #: its range is weaker evidence than one held by three.
    station_ids: str
    #: True when the vessel WAS transmitting and simply out of AIS reception.
    #: These are the honest non-findings: calling one dark would be the
    #: offshore-silence anti-pattern, so the measurement scores them as
    #: correctly-suppressed rather than as misses.
    explainable_by_coverage: bool = False
    #: A hull that is legitimately dark and that **no sensor-level rule can
    #: exclude** — a naval unit operating without AIS, which is normal practice.
    #: Radar sees an unexplained contact and is right to; the product is wrong
    #: to alert on it, and there is nothing in the radar picture that could tell
    #: the difference. Recorded rather than quietly excused, because the size of
    #: this population is precisely the argument for a known-units layer, and
    #: that argument needs a number.
    unavoidable_false_positive: bool = False
    #: Whether we believe a correctly built system should produce a contact
    #: here. False with a stated reason is a recorded capability boundary.
    expected_detection: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if self.cause not in CAUSES:
            raise ValueError(f"cause must be one of {CAUSES}, got {self.cause!r}")
        if self.t_end < self.t_start:
            raise ValueError(f"{self.episode_id}: window runs backwards")
        if self.expected_detection and self.explainable_by_coverage:
            raise ValueError(
                f"{self.episode_id}: an episode explainable by AIS coverage "
                f"must not be expected to fire — flagging it would be the "
                f"out-of-coverage-is-not-dark anti-pattern (CLAUDE.md §6).")
        if self.expected_detection and self.unavoidable_false_positive:
            raise ValueError(
                f"{self.episode_id}: an episode marked as an unavoidable false "
                f"positive cannot also be one we want found. The product policy "
                f"is that a naval unit must never be flagged (DX7); the radar "
                f"answer key has to agree with it or the two measurements will "
                f"score the same contact both ways.")

    def as_row(self) -> dict:
        return dict(
            episode_id=self.episode_id,
            entity_id=self.entity_id,
            t_start=self.t_start, t_end=self.t_end,
            lat=self.lat, lon=self.lon,
            lat_min=self.lat_min, lat_max=self.lat_max,
            lon_min=self.lon_min, lon_max=self.lon_max,
            length_m=self.length_m,
            cause=self.cause,
            n_plots=self.n_plots,
            duration_min=self.duration_min,
            station_ids=self.station_ids,
            explainable_by_coverage=self.explainable_by_coverage,
            unavoidable_false_positive=self.unavoidable_false_positive,
            expected_detection=self.expected_detection,
            notes=self.notes,
        )


class RadarTruthLedger:
    """Collects dark episodes. Refuses duplicates, like the scenario ledger."""

    def __init__(self):
        self._rows: dict[str, RadarDarkEpisode] = {}

    def add(self, ep: RadarDarkEpisode) -> RadarDarkEpisode:
        if ep.episode_id in self._rows:
            raise ValueError(f"duplicate radar episode id {ep.episode_id!r}")
        self._rows[ep.episode_id] = ep
        return ep

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(sorted(self._rows.values(), key=lambda r: r.episode_id))

    def rows(self) -> list[dict]:
        return [r.as_row() for r in self]

    def summary(self) -> dict:
        eps = list(self)
        findable = [e for e in eps if e.expected_detection]
        return dict(
            episodes=len(eps),
            expected_to_fire=len(findable),
            explainable_by_coverage=sum(1 for e in eps
                                        if e.explainable_by_coverage),
            unavoidable_false_positives=sum(
                1 for e in eps if e.unavoidable_false_positive),
            by_cause={c: sum(1 for e in eps if e.cause == c) for c in CAUSES
                      if any(e.cause == c for e in eps)},
            total_dark_hours=round(
                sum(e.duration_min for e in eps) / 60.0, 1),
        )
