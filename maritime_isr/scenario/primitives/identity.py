"""identity_event_primitive — names, flags, MMSIs and IMOs that change over time.

A vessel identity is an **interval**, not a value. "OCEAN TRADER, flag Panama" is
true between two dates and false outside them, and the whole B-group of scenarios
turns on getting that right: B6's voyage-specific reflagging is invisible unless
the edges are time-scoped, and B3's flag cascade is an anomaly in the *rate* of
change, which cannot be measured without dated intervals.

**Interval closing is the load-bearing part.** When an identity changes, the old
interval's `valid_to` is set to the change time and the new interval opens at the
same instant. Two consequences, both deliberate:

  * There is never a moment with two open identities of the same kind, so a
    query for "what was this vessel called on 14 June" has exactly one answer.
  * There is never a gap between them either, so the vessel does not blink out
    of existence at the change.

This mirrors the real corpus's failure mode in reverse. On live GFW data, 8,724
of 8,724 identity intervals came back closed, and reading closure as
supersession labelled the entire fleet as having changed identity (STATE.md).
Here the generator knows which closures are real supersessions and which are
merely the end of the window, and the landed rows carry that distinction
explicitly — so `from_landed.add_identities` can be exercised on data where the
right answer is known.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: What changed. A single event can change several at once — a phoenix
#: reappearance changes name, flag and MMSI together.
FIELD_NAME = "name"
FIELD_FLAG = "flag"
FIELD_MMSI = "mmsi"
FIELD_IMO = "imo"
FIELD_CALL_SIGN = "call_sign"

IDENTITY_FIELDS = (FIELD_NAME, FIELD_FLAG, FIELD_MMSI, FIELD_IMO,
                   FIELD_CALL_SIGN)


@dataclass
class IdentityInterval:
    """One identity fact, scoped in time.

    `superseded` records whether this interval ended because the identity
    genuinely changed, or merely because the observation window did. That is the
    exact distinction the real-data populator had to learn the hard way, and
    landing it explicitly means the generated corpus can prove the populator
    handles it.
    """
    vessel_entity_id: str
    field_name: str
    value: object
    valid_from: datetime
    valid_to: datetime | None = None
    superseded: bool = False
    reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.valid_to is None


@dataclass
class IdentityEvent:
    """A change, with what it was before and after."""
    vessel_entity_id: str
    t: datetime
    changes: dict            # field -> (old, new)
    reason: str = ""


@dataclass
class IdentityLedger:
    """Every identity interval for every vessel, kept consistent.

    The ledger is the only thing allowed to open or close an interval, which is
    what keeps the no-overlap and no-gap guarantees true by construction rather
    than by careful call sites.
    """
    intervals: list[IdentityInterval] = field(default_factory=list)
    events: list[IdentityEvent] = field(default_factory=list)

    def open_initial(self, vessel, t0: datetime) -> None:
        """Seed a vessel's starting identity at the beginning of the window."""
        for f, v in ((FIELD_NAME, vessel.name), (FIELD_FLAG, vessel.flag),
                     (FIELD_MMSI, vessel.mmsi), (FIELD_IMO, vessel.imo),
                     (FIELD_CALL_SIGN, vessel.call_sign)):
            self.intervals.append(IdentityInterval(
                vessel.entity_id, f, v, t0, reason="initial"))

    def current(self, entity_id: str, field_name: str,
                at: datetime | None = None) -> IdentityInterval | None:
        best = None
        for iv in self.intervals:
            if iv.vessel_entity_id != entity_id or iv.field_name != field_name:
                continue
            if at is not None:
                if iv.valid_from > at:
                    continue
                if iv.valid_to is not None and iv.valid_to <= at:
                    continue
            if best is None or iv.valid_from > best.valid_from:
                best = iv
        return best

    def change(self, vessel, t: datetime, changes: dict, *,
               reason: str = "") -> IdentityEvent:
        """Apply an identity change: close the old intervals, open the new.

        Mutates the vessel's current identity fields too, so anything generated
        after this point — AIS rows, event rows — carries the new identity
        automatically. That coupling is what makes B1's reappearance consistent
        across every table rather than only in the identity ledger.
        """
        applied: dict = {}
        for f, new in changes.items():
            if f not in IDENTITY_FIELDS:
                raise ValueError(f"not an identity field: {f!r}")
            cur = self.current(vessel.entity_id, f, at=t)
            old = cur.value if cur else getattr(vessel, f, None)
            if old == new:
                continue
            if cur is not None and cur.is_open:
                cur.valid_to = t
                cur.superseded = True
                cur.reason = cur.reason or reason
            self.intervals.append(IdentityInterval(
                vessel.entity_id, f, new, t, reason=reason))
            setattr(vessel, f, new)
            applied[f] = (old, new)

        ev = IdentityEvent(vessel.entity_id, t, applied, reason=reason)
        self.events.append(ev)
        vessel.identity_history.append(ev)
        return ev

    def close_window(self, t_end: datetime) -> None:
        """Close every still-open interval at the end of the corpus window.

        **Marked `superseded=False`** — the identity did not change, our
        observation stopped. Landing these as supersessions is the exact bug
        that made 100% of the real fleet look like it had been renamed, and
        having both kinds in the corpus is what lets a test prove the populator
        tells them apart.
        """
        for iv in self.intervals:
            if iv.is_open:
                iv.valid_to = t_end
                iv.superseded = False

    def change_count(self, entity_id: str, field_name: str) -> int:
        """How many times a field genuinely changed — B3's rate anomaly."""
        return sum(1 for iv in self.intervals
                   if iv.vessel_entity_id == entity_id
                   and iv.field_name == field_name and iv.superseded)

    def for_vessel(self, entity_id: str) -> list[IdentityInterval]:
        return sorted((iv for iv in self.intervals
                       if iv.vessel_entity_id == entity_id),
                      key=lambda iv: (iv.field_name, iv.valid_from))

    def snapshot_at(self, entity_id: str, at: datetime) -> dict:
        """Every identity field as it stood at `at`."""
        return {f: (iv.value if (iv := self.current(entity_id, f, at)) else None)
                for f in IDENTITY_FIELDS}


def assert_consistent(ledger: IdentityLedger) -> list[str]:
    """No overlaps, no gaps, no field with two open intervals. Empty is good."""
    problems: list[str] = []
    by_key: dict[tuple, list[IdentityInterval]] = {}
    for iv in ledger.intervals:
        by_key.setdefault((iv.vessel_entity_id, iv.field_name), []).append(iv)

    for (eid, f), ivs in sorted(by_key.items()):
        ivs = sorted(ivs, key=lambda i: i.valid_from)
        opens = [i for i in ivs if i.is_open]
        if len(opens) > 1:
            problems.append(f"{eid}/{f}: {len(opens)} open intervals at once")
        for a, b in zip(ivs, ivs[1:]):
            if a.valid_to is None:
                problems.append(f"{eid}/{f}: interval from {a.valid_from} never "
                                f"closed but is followed by another")
                continue
            if a.valid_to > b.valid_from:
                problems.append(f"{eid}/{f}: intervals overlap at {b.valid_from}")
            elif a.valid_to < b.valid_from:
                problems.append(f"{eid}/{f}: gap between {a.valid_to} and "
                                f"{b.valid_from} — identity blinked out")
    return problems
