"""The container every scenario writes into, and the corpus window it lives in.

`ScenarioWorld` is the single accumulator: vessels, corporate structure,
identity intervals, integrated tracks, emitted AIS, derived events, synthetic
SAR contacts and truth rows. Scenarios append to it; `land.py` writes it out;
`validate.py` checks it. Nothing else holds generated state, so there is exactly
one place to look for what a run produced.

**The window is the real corpus window.** 2026-06-04 to 2026-07-30 is the span
the landed GFW events actually cover, and generating outside it would put
scenario events in weeks where the real data has nothing to say — making any
combined view obviously bimodal and any "same tables" claim hollow. Everything
is clipped to it on the way in, and the validator re-checks on the way out.

**Serial numbers, not RNG draws, mint identifiers.** A vessel's IMO and MMSI
come from its position in the cast. That means adding a scenario later does not
renumber every existing hull, so a corpus generated at seed N stays comparable
with one generated at seed N last week even after the catalogue grows.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .primitives.ais import AisReport
from .primitives.identity import IdentityLedger
from .primitives.org import CorporateWorld, build_agents_and_addresses
from .primitives.track import TrackPoint
from .primitives.vessel import SyntheticVessel
from .truth import TruthLedger

#: The real corpus window. Every generated event lands inside it.
#:
#: T1 is the **real corpus maximum**, measured from the operator's landed
#: events: 2026-07-25 22:53. The generator previously ran to 07-30, five days
#: past the last real event, which would have put scenario rows in day
#: partitions where the real data has nothing at all — making the combined
#: corpus visibly bimodal at the tail and any "same tables" claim hollow.
#:
#: The real corpus *starts* in 2012 (a handful of long-tail identity and
#: loitering records), but the eight-week narrative deliberately sits at the
#: dense end where the bulk of the real events are.
T0 = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 25, 22, 0, tzinfo=timezone.utc)

WINDOW_DAYS = (T1 - T0).days


def week(n: int, *, hours: float = 0.0) -> datetime:
    """Start of scenario week `n` (1-based), optionally offset by hours.

    The temporal choreography in the build plan is written in weeks, so the
    scenarios read the same way: `week(3, hours=14)` is where A1 fires.
    """
    if not 1 <= n <= 9:
        raise ValueError(f"week {n} is outside the 8-week corpus window")
    return T0 + timedelta(days=7 * (n - 1), hours=hours)


def in_window(t: datetime) -> bool:
    return T0 <= t <= T1


@dataclass
class SarContact:
    """A synthetic SAR detection.

    Used only by scenarios that need imagery to *contradict* AIS (A3's
    spoof-and-swap, C2's empty berth). It is a contact with a position, a time
    and a length estimate — the same shape a CFAR detector emits — not a
    picture. We have run no SAR (ADR-017) and this does not change that: it is a
    fixture for the fusion path, and it lands flagged synthetic like everything
    else.
    """
    detection_id: str
    lat: float
    lon: float
    t: datetime
    length_m: float
    scene_id: str
    #: Truth-side only, never landed: which vessel it actually is, or None for
    #: a contact with no AIS counterpart.
    truth_entity_id: str | None = None


@dataclass
class LandedEvent:
    """A derived behaviour event in the shape the GFW connectors land.

    Scenarios produce these directly rather than having a detector infer them,
    for the same reason the real corpus contains them: GFW's events *are* the
    behavioural layer this system consumes. Generating the AIS and then
    re-deriving events from it would test our own event derivation, which does
    not exist — the fusion core consumes landed events.
    """
    kind: str                       # encounters | loitering | port_visits | gaps
    event_id: str
    entity_id: str
    t_start: datetime
    t_end: datetime
    lat: float
    lon: float
    props: dict = field(default_factory=dict)
    counterpart_entity_id: str | None = None


@dataclass
class ScenarioWorld:
    seed: int
    rng: random.Random
    profile: object
    t0: datetime = T0
    t1: datetime = T1

    vessels: dict[str, SyntheticVessel] = field(default_factory=dict)
    corporate: CorporateWorld = field(default_factory=CorporateWorld)
    identity: IdentityLedger = field(default_factory=IdentityLedger)
    truth: TruthLedger = field(default_factory=TruthLedger)

    #: Ground-truth integrated motion, per vessel. Several segments per vessel
    #: are normal — a vessel does more than one thing in eight weeks.
    tracks: dict[str, list[TrackPoint]] = field(default_factory=dict)
    #: Emitted AIS, per vessel. This is what lands as positions.
    ais: dict[str, list[AisReport]] = field(default_factory=dict)

    events: list[LandedEvent] = field(default_factory=list)
    sar_contacts: list[SarContact] = field(default_factory=list)
    #: Synthetic sanctions listings, on the fictional SCENARIO-SDN list.
    sanctions: list[dict] = field(default_factory=list)

    #: Set by scenarios that clipped something, so the report can say so.
    clipped: list[str] = field(default_factory=list)

    _next_serial: int = 0
    #: entity_id -> [(t_start, t_end, label)] — the occupancy calendar that
    #: keeps one hull from being in two places at once.
    _segments: dict = field(default_factory=dict, repr=False)
    #: Set by `land_world`; carries the achieved-vs-real null rates.
    null_mask: object = None

    #: The simulated coastal-radar picture, once `scenario.radar` has run over
    #: this world (ADR-028). It is not a scenario output: it is *derived from*
    #: the tracks the scenarios already wrote, which is the whole point — one
    #: vessel truth, two sensors that disagree about it. `None` until generated.
    radar: object = None

    # ---- construction ----
    @classmethod
    def new(cls, seed: int, profile) -> "ScenarioWorld":
        w = cls(seed=seed, rng=random.Random(seed), profile=profile)
        build_agents_and_addresses(w.corporate)
        return w

    def take_serial(self) -> int:
        s = self._next_serial
        self._next_serial += 1
        return s

    # ---- vessels ----
    def add_vessel(self, v: SyntheticVessel) -> SyntheticVessel:
        if v.entity_id in self.vessels:
            raise ValueError(f"duplicate vessel entity id {v.entity_id!r}")
        self.vessels[v.entity_id] = v
        self.identity.open_initial(v, self.t0)
        return v

    def vessel(self, entity_id: str) -> SyntheticVessel:
        return self.vessels[entity_id]

    # ---- motion ----
    def add_track(self, entity_id: str, points: list[TrackPoint],
                  *, allow_overlap: bool = False) -> list[TrackPoint]:
        """Append integrated motion, clipped to the corpus window.

        **Refuses to put one hull in two places at the same instant.** Vessels
        are shared across scenarios on purpose — that is what gives the graph
        something to traverse — but sharing a vessel means sharing its
        *calendar*, and two scenarios that both move her on the same afternoon
        produce a track that teleports between them every minute.

        This started as a turn-rate violation of 343 deg/s and was only
        traceable back to overlapping segments after some digging, which is the
        argument for catching it here instead: an overlap is a scheduling
        mistake by a scenario author, and it should fail at the line that made
        it rather than as an unexplained physics artefact several steps later.

        `allow_overlap` exists for the one legitimate case — B5's MMSI clone,
        where two *different* hulls share an identity, not a hull sharing a
        calendar — and is not used for anything else.
        """
        kept = [p for p in points if in_window(p.t)]
        if len(kept) != len(points):
            self.clipped.append(
                f"{entity_id}: {len(points) - len(kept)} track point(s) outside "
                f"the corpus window")
        if kept and not allow_overlap:
            t0, t1 = kept[0].t, kept[-1].t
            # Position continuity: she has to have been able to *get* here.
            # Occupancy alone only stops two segments overlapping in time; a
            # scenario that starts a vessel at a fixed position hours after
            # another scenario left her 300 nm away produces a teleport that no
            # time check would see. Measured at 50.9 kn on an Aframax before
            # this existed.
            prev = self.tracks.get(entity_id)
            v = self.vessels.get(entity_id)
            if prev and v is not None:
                last = max(prev, key=lambda p: p.t)
                dt_h = (t0 - last.t).total_seconds() / 3600.0
                if dt_h > 0:
                    from .geography import haversine_m
                    need_kn = (haversine_m(last.lat, last.lon,
                                           kept[0].lat, kept[0].lon)
                               / 1852.0 / dt_h)
                    if need_kn > v.max_kn * 1.05:
                        raise ValueError(
                            f"{entity_id} cannot reach the start of the new "
                            f"segment: {need_kn:.1f} kn needed over {dt_h:.1f} h "
                            f"from her last position, class max {v.max_kn} kn. "
                            f"Schedule the scenario later, or start it from "
                            f"where she actually is.")
            for s0, s1, label in self._segments.get(entity_id, []):
                # Touching endpoints are fine: one leg ending as the next begins
                # is exactly how a voyage is assembled.
                if t0 < s1 and s0 < t1:
                    raise ValueError(
                        f"{entity_id} would be in two places at once: new "
                        f"segment {t0:%Y-%m-%d %H:%M}..{t1:%Y-%m-%d %H:%M} "
                        f"overlaps existing {s0:%Y-%m-%d %H:%M}.."
                        f"{s1:%Y-%m-%d %H:%M}{f' ({label})' if label else ''}. "
                        f"Reschedule one of the two scenarios using her.")
            self._segments.setdefault(entity_id, []).append((t0, t1, ""))
        self.tracks.setdefault(entity_id, []).extend(kept)
        return kept

    def occupied(self, entity_id: str) -> list[tuple[datetime, datetime]]:
        """When this vessel is already busy — for scheduling a new scenario."""
        return [(a, b) for a, b, _ in self._segments.get(entity_id, [])]

    def free_from(self, entity_id: str) -> datetime | None:
        """The instant after her last committed segment, or None if idle."""
        segs = self._segments.get(entity_id, [])
        return max(b for _, b, _ in segs) if segs else None

    def add_ais(self, entity_id: str, reports: list[AisReport]) -> list[AisReport]:
        kept = [r for r in reports if in_window(r.t)]
        if len(kept) != len(reports):
            self.clipped.append(
                f"{entity_id}: {len(reports) - len(kept)} AIS report(s) outside "
                f"the corpus window")
        self.ais.setdefault(entity_id, []).extend(kept)
        return kept

    def track_of(self, entity_id: str) -> list[TrackPoint]:
        return sorted(self.tracks.get(entity_id, []), key=lambda p: p.t)

    def ais_of(self, entity_id: str) -> list[AisReport]:
        return sorted(self.ais.get(entity_id, []), key=lambda r: r.t)

    # ---- events ----
    def add_event(self, ev: LandedEvent) -> LandedEvent:
        if not (in_window(ev.t_start) and in_window(ev.t_end)):
            raise ValueError(
                f"event {ev.event_id} runs outside the corpus window "
                f"({ev.t_start} .. {ev.t_end})")
        self.events.append(ev)
        return ev

    def add_sar(self, c: SarContact) -> SarContact:
        self.sar_contacts.append(c)
        return c

    def add_sanction(self, entry: dict) -> dict:
        self.sanctions.append(entry)
        return entry

    # ---- reporting ----
    def counts(self) -> dict:
        by_kind: dict[str, int] = {}
        for e in self.events:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        radar = self.radar.counts() if self.radar is not None else {}
        return dict(**radar, **dict(
            vessels=len(self.vessels),
            organizations=len(self.corporate.orgs),
            ownership_edges=len(self.corporate.edges),
            identity_intervals=len(self.identity.intervals),
            identity_events=len(self.identity.events),
            track_points=sum(len(v) for v in self.tracks.values()),
            ais_reports=sum(len(v) for v in self.ais.values()),
            sar_contacts=len(self.sar_contacts),
            sanctions_entries=len(self.sanctions),
            scenarios=len(self.truth),
            **{f"events_{k}": v for k, v in sorted(by_kind.items())},
        ))

    def all_identifiers(self) -> tuple[list, list, list]:
        """(imos, mmsis, sanctions refs) actually used — for the collision guard.

        Includes historical MMSIs from the identity ledger, not just current
        ones. A phoenix vessel has worn two MMSIs and both must be checked; a
        guard that only saw the current identity would let the discarded one
        collide unnoticed.
        """
        imos = {v.imo for v in self.vessels.values()}
        mmsis = {v.mmsi for v in self.vessels.values()}
        for iv in self.identity.intervals:
            if iv.field_name == "imo" and iv.value is not None:
                imos.add(iv.value)
            if iv.field_name == "mmsi" and iv.value is not None:
                mmsis.add(iv.value)
        refs = {s.get("entry_id") for s in self.sanctions if s.get("entry_id")}
        return sorted(imos), sorted(mmsis), sorted(refs)
