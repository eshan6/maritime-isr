"""What the form declares against what the track shows — Area 4.

*"Then generate the risk intelligence, which is the actual point: compare what
the notification declares against what the track shows. Declared cargo against
behaviour. Declared last port against where the vessel actually was. Declared
arrival window against observed movement. Declared crew and ownership against
registry and lists."* — the IDEX Challenge 82 brief, Area 4.

Pure functions with three-valued outcomes, the same shape `anomaly.identity` and
`anomaly.voyage` use, and for the same reason: **"we could not check" is an
answer.** Real notifications are incomplete — a fifth of them omit a field, a
fax loses two more to OCR — and a rule that folded "the form did not say" into
"the form was fine" would report a clean inbox it had never read.

Four checks, ordered by how much of the claim is arithmetic:

1. **Declared last port against where she was.** She says she sailed from
   Karachi; her own AIS puts her 1,500 km away that week. This is the strongest
   of the four because it compares one assertion against a measured position,
   with only a radius as judgement.
2. **Declared arrival window against observed arrival.** Filed 24-96 hours
   ahead, so an estimate that slips is ordinary — the rule fires on a gap no
   schedule explains, not on any gap at all. Same shape as the ETA rule in
   `anomaly.voyage`, and it inherits that rule's lesson about late vessels.
3. **Declared owner against the designation lists.** Not a contradiction at all
   but a *join* the paperwork makes possible: a form names an owner AIS never
   carries, and that name can be matched against a sanctions list.
4. **Declared cargo against behaviour.** The weakest, and deliberately narrow.

**On cargo, what is NOT built and why.** "Declared cargo against behaviour" is
the most tempting of the four and the least defensible in general: a bulker
declaring cement and riding high could be in ballast, part-laden, or lying, and
motion alone does not separate those. What *is* checked is the one case where
the paperwork contradicts itself against the physics without needing a cargo
model — a hull declaring "Ballast — no cargo" while broadcasting a laden
draught. That is arithmetic, not inference. The general case is recorded as a
gap rather than approximated, because a cargo rule that fires on honest ballast
voyages would be the alert-fatigue failure ADR-004 exists to prevent.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Optional

from ..ports import PORTS

__all__ = ["PaperFinding", "check_last_port", "check_arrival_window",
           "check_declared_ballast", "LAST_PORT_RADIUS_KM",
           "ARRIVAL_SLIP_HOURS"]


#: How near a port she has to have been for "I sailed from there" to hold.
#:
#: Generous on purpose. A port call is recorded at a berth, but a vessel that
#: sailed from Kandla and was first heard 60 km down the coast has still sailed
#: from Kandla — the gazetteer holds one coordinate for a port area that is
#: tens of kilometres across, and terrestrial AIS does not hear her until she
#: is well out. The rule has to survive both.
LAST_PORT_RADIUS_KM = 120.0

#: How long a declared arrival may slip before it stops being an estimate.
#:
#: A notification is filed 24-96 hours ahead. Weather, a tide, a pilot and a
#: berth that is not free are all inside a day, and **the corpus contains a
#: decoy that misses by six hours precisely so this floor cannot quietly be
#: tightened** (P5). Firing on a six-hour slip would fire on the whole fleet.
ARRIVAL_SLIP_HOURS = 24.0

#: Declared-ballast phrases. Free text, so this matches a phrase rather than a
#: code — and it matches only unambiguous ones. "Part cargo" and "light" are
#: deliberately absent: both are used loosely enough that a match would be a
#: guess about what an agent meant.
BALLAST_PHRASES = ("BALLAST", "NO CARGO", "NIL CARGO", "IN BALLAST",
                   "LIGHT SHIP")

#: Draught above which a hull is plainly carrying something. Well above any
#: ballast condition for the classes in this AOI — a laden VLCC draws 20 m and
#: the same hull in ballast draws about 9, so 12 accuses nobody who is empty.
LADEN_DRAUGHT_M = 12.0


class PaperFinding:
    """One statement about a notification, with its verdict.

    Three-valued: ``contradiction`` | ``ok`` | ``not_checkable``. The evidence
    carries the *passage* the declared value came from, because the brief is
    explicit that a field which cannot be traced back to its source text is not
    usable as evidence — and a finding is exactly where that matters.
    """

    __slots__ = ("check", "outcome", "confidence", "statement", "detail",
                 "passage", "locator")

    def __init__(self, check: str, outcome: str, confidence: float,
                 statement: str, detail: dict | None = None,
                 passage: Optional[str] = None,
                 locator: Optional[str] = None):
        self.check = check
        self.outcome = outcome
        self.confidence = confidence
        self.statement = statement
        self.detail = detail or {}
        self.passage = passage
        self.locator = locator

    @property
    def is_contradiction(self) -> bool:
        return self.outcome == "contradiction"

    def as_dict(self) -> dict:
        return {"check": self.check, "outcome": self.outcome,
                "confidence": round(self.confidence, 3),
                "statement": self.statement,
                "passage": self.passage, "locator": self.locator,
                **self.detail}

    def __repr__(self) -> str:                                # pragma: no cover
        return f"<PaperFinding {self.check} {self.outcome}>"


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _port_of(text) -> Optional[str]:
    """Resolve a declared port name, reusing the voyage rule's resolver.

    Shared deliberately: a form saying "NHAVA SHEVA" and an AIS message saying
    "NHAVA SHEVA" must resolve to the same place, or the two Area 2 and Area 4
    rules would disagree about where a vessel said she was going.
    """
    from .voyage import resolve_destination
    return resolve_destination(text)


# --------------------------------------------------------------------------
# 1. declared last port against where she actually was
# --------------------------------------------------------------------------

def check_last_port(*, declared, fixes, filed_at,
                    prior_calls=()) -> PaperFinding:
    """Was she ever near the port she says she sailed from?

    `fixes` is ``(t, lat, lon)`` covering the days before the notification was
    filed. The window matters: a vessel that called at Karachi last month and
    at Kandla last week has been at both, and the claim on the form is about
    the *last* one.

    `prior_calls` is ``[(lat, lon)]`` for every call the corpus recorded for
    her before filing. **It is preferred to the nearest-approach test, because
    it compares like with like.** The gazetteer holds a single coordinate for a
    port area tens of kilometres across, and the port-call detector records a
    berthing wherever she actually stopped — which in this corpus is up to
    150 km from that coordinate. Measuring a declaration against the gazetteer
    point accused a hull of lying about Karachi when her recorded call *was*
    Karachi, 144 km from the pin.
    """
    field = declared.get("last_port")
    raw = field.value if field else None
    port = _port_of(raw)
    if port is None:
        return PaperFinding(
            "declared_last_port", "not_checkable", 0.0,
            (f"The form gives a last port of {raw!r}, which does not resolve to "
             f"a place in the gazetteer." if raw else
             "The form does not state a last port."),
            passage=field.passage if field else None,
            locator=field.locator if field else None)

    pts = [f for f in fixes if f is not None]
    if len(pts) < 3:
        return PaperFinding(
            "declared_last_port", "not_checkable", 0.0,
            f"She declares {port}, and we hold too little track before the "
            f"notification to say where she had been.",
            passage=field.passage, locator=field.locator)

    plat, plon = PORTS[port]
    if prior_calls:
        # **Did she call there at all — not "was her most recent call there".**
        # A vessel that sailed from Mundra and then made an unnamed offshore
        # stop has still sailed from Mundra, and taking the latest call by time
        # accused her of lying about a port she had genuinely just left. This is
        # the same join error as matching an arrival by time alone, mirrored:
        # the claim names a call, so the call has to be found by name.
        nearest = min(_km(la, lo, plat, plon) for la, lo in prior_calls)
        if nearest <= LAST_PORT_RADIUS_KM:
            return PaperFinding(
                "declared_last_port", "ok", 0.0,
                f"She declares {port}, and one of the calls we recorded before "
                f"she filed was {nearest:.0f} km from it.",
                dict(port=port, nearest_call_km=round(nearest, 1)),
                passage=field.passage, locator=field.locator)
        conf = min(0.9, 0.5 + 0.05 * (nearest / LAST_PORT_RADIUS_KM))
        return PaperFinding(
            "declared_last_port", "contradiction", round(conf, 3),
            f"The notification declares {port} as her last port. Not one of "
            f"the calls we recorded for her before filing comes within "
            f"{nearest:.0f} km of it.",
            dict(port=port, nearest_call_km=round(nearest, 1),
                 n_prior_calls=len(prior_calls), filed_at=str(filed_at)),
            passage=field.passage, locator=field.locator)

    nearest = min(_km(la, lo, plat, plon) for _, la, lo in pts)
    if nearest <= LAST_PORT_RADIUS_KM:
        return PaperFinding(
            "declared_last_port", "ok", 0.0,
            f"She declares {port} and came within {nearest:.0f} km of it "
            f"before filing.",
            dict(port=port, nearest_km=round(nearest, 1)),
            passage=field.passage, locator=field.locator)

    # Confidence rises with the size of the lie, capped: 200 km could be a
    # coastal port we hold one coordinate for; 1,500 km is a different sea.
    conf = min(0.9, 0.5 + 0.05 * (nearest / LAST_PORT_RADIUS_KM))
    return PaperFinding(
        "declared_last_port", "contradiction", round(conf, 3),
        f"The notification declares {port} as her last port. Her own track "
        f"never brings her closer than {nearest:.0f} km to it in the days "
        f"before filing.",
        dict(port=port, nearest_km=round(nearest, 1),
             filed_at=str(filed_at)),
        passage=field.passage, locator=field.locator)


# --------------------------------------------------------------------------
# 2. declared arrival window against observed arrival
# --------------------------------------------------------------------------

def match_arrival(declared, arrivals, filed_at) -> Optional[object]:
    """Which recorded arrival this notification is about, or None.

    **The next arrival in time is not the arrival the form is about**, and
    assuming it was is how this rule produced thirty contradictions in a single
    run. A notification is filed 24-96 hours ahead; a working coastal vessel
    frequently makes another call inside that window. Comparing her declared ETA
    against whichever stop happened first afterwards then measures the gap
    between one voyage's estimate and a different voyage's berthing — which
    comes out as "arrived 31-65 hours early" on thirty honest hulls, every one
    of them early, which is the shape of a join error rather than of deceit.

    So the match is on the **declared arrival port**: the first recorded call at
    the port the form names, at or after filing. A form naming a port she never
    reaches has no arrival to be measured against, and that is `None` — not a
    contradiction, because a voyage that was diverted or abandoned is not a
    false declaration about its ETA.

    `arrivals` is a sequence of `(timestamp, port_name)`.
    """
    field = declared.get("arrival_port")
    port = _port_of(field.value if field else None)
    if port is None:
        return None
    for when, name in sorted(arrivals, key=lambda a: a[0]):
        if filed_at is not None and when < filed_at:
            continue
        if _port_of(name) == port:
            return when
    return None


def check_arrival_window(*, declared, observed_arrival) -> PaperFinding:
    """Did she arrive anywhere near when she said she would?

    `observed_arrival` is the arrival this notification is about — selected by
    `match_arrival`, not simply the next one in time — or None when there is no
    such arrival. **A vessel that has not arrived is not late**: she may still
    be on passage, and the notification's claim is about the future until the
    arrival happens.
    """
    field = declared.get("eta")
    raw = field.value if field else None
    if not raw:
        return PaperFinding(
            "declared_arrival_window", "not_checkable", 0.0,
            "The form states no arrival time.",
            passage=field.passage if field else None,
            locator=field.locator if field else None)
    from datetime import datetime
    try:
        eta = datetime.fromisoformat(str(raw))
    except ValueError:
        return PaperFinding(
            "declared_arrival_window", "not_checkable", 0.0,
            f"The arrival time on the form, {raw!r}, could not be parsed. The "
            f"passage is kept so an operator can read what the parser could "
            f"not.",
            passage=field.passage, locator=field.locator)
    if observed_arrival is None:
        return PaperFinding(
            "declared_arrival_window", "not_checkable", 0.0,
            "She has not arrived. A declared arrival is a claim about the "
            "future until it happens.",
            passage=field.passage, locator=field.locator)

    slip_h = (observed_arrival - eta).total_seconds() / 3600.0
    if abs(slip_h) <= ARRIVAL_SLIP_HOURS:
        return PaperFinding(
            "declared_arrival_window", "ok", 0.0,
            f"Declared {eta:%d %b %H:%M}, arrived "
            f"{observed_arrival:%d %b %H:%M} — {abs(slip_h):.0f} hours out, "
            f"which is what an estimate filed days ahead is.",
            dict(slip_hours=round(slip_h, 1)),
            passage=field.passage, locator=field.locator)

    conf = min(0.85, 0.45 + 0.01 * (abs(slip_h) - ARRIVAL_SLIP_HOURS))
    early = "before" if slip_h < 0 else "after"
    return PaperFinding(
        "declared_arrival_window", "contradiction", round(conf, 3),
        f"The notification declares an arrival at {eta:%d %b %H:%M}; she "
        f"berthed {abs(slip_h):.0f} hours {early} that, at "
        f"{observed_arrival:%d %b %H:%M}. A filing is an estimate, and an "
        f"estimate is not out by {abs(slip_h) / 24:.1f} days.",
        dict(slip_hours=round(slip_h, 1),
             declared=eta.isoformat(), observed=observed_arrival.isoformat()),
        passage=field.passage, locator=field.locator)


# --------------------------------------------------------------------------
# 3. declared ballast against a laden draught
# --------------------------------------------------------------------------

def check_declared_ballast(*, declared, draught_m) -> PaperFinding:
    """"No cargo" on the form, and a laden draught on the air.

    The one cargo check that is arithmetic rather than inference. The general
    case — declared commodity against behaviour — is **not built**; see the
    module docstring for why approximating it would fire on honest ballast
    voyages.
    """
    field = declared.get("cargo")
    raw = (field.value if field else None) or ""
    if not raw:
        return PaperFinding(
            "declared_ballast", "not_checkable", 0.0,
            "The form does not state a cargo.",
            passage=field.passage if field else None,
            locator=field.locator if field else None)
    if not any(p in raw.upper() for p in BALLAST_PHRASES):
        return PaperFinding(
            "declared_ballast", "not_checkable", 0.0,
            f"She declares {raw!r}. Comparing a named commodity against motion "
            f"needs a cargo model this system does not have, and guessing one "
            f"would fire on honest voyages.",
            passage=field.passage, locator=field.locator)
    if draught_m is None:
        return PaperFinding(
            "declared_ballast", "not_checkable", 0.0,
            f"She declares {raw!r} and broadcast no draught to compare it "
            f"against.",
            passage=field.passage, locator=field.locator)
    if float(draught_m) < LADEN_DRAUGHT_M:
        return PaperFinding(
            "declared_ballast", "ok", 0.0,
            f"She declares {raw!r} and is drawing {float(draught_m):.1f} m, "
            f"which is consistent with an empty hull.",
            dict(draught_m=float(draught_m)),
            passage=field.passage, locator=field.locator)
    return PaperFinding(
        "declared_ballast", "contradiction", 0.7,
        f"The notification declares {raw!r} while she broadcasts a draught of "
        f"{float(draught_m):.1f} m. A hull in ballast does not draw that.",
        dict(draught_m=float(draught_m), declared_cargo=raw),
        passage=field.passage, locator=field.locator)


def check_paperwork(*, declared, fixes=(), filed_at=None,
                    observed_arrival=None, draught_m=None,
                    prior_calls=()) -> list[PaperFinding]:
    """Every check over one notification, cheapest and surest first."""
    return [
        check_last_port(declared=declared, fixes=fixes, filed_at=filed_at,
                        prior_calls=prior_calls),
        check_arrival_window(declared=declared,
                             observed_arrival=observed_arrival),
        check_declared_ballast(declared=declared, draught_m=draught_m),
    ]


def window_before(fixes, filed_at, *, days: float = 10.0):
    """The track in the days before a notification was filed.

    The claim on a form is about the voyage she is on, not about her year. A
    vessel that called at Karachi six weeks ago and sails today from Kandla has
    been to both, and comparing against her whole history would make every
    declared last port true.
    """
    if filed_at is None:
        return list(fixes)
    lo = (filed_at - timedelta(days=days)).timestamp()
    hi = filed_at.timestamp()
    return [f for f in fixes if lo <= f[0] <= hi]
