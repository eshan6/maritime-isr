"""Does what she says about her voyage match what she does? — Area 2.

*"Compare the destination the vessel declares against the destination its
behaviour implies, and against where it has historically gone — a declared
destination that the track has never been consistent with is one of the
strongest and simplest suspicion factors available. Do the same with declared
arrival time against plausible arrival time given current position and speed."*
— the IDEX Challenge 82 brief, Area 2.

This was the clearest thing the brief asked for that the system did not have,
and the reason was upstream of the rule: **nothing landed a declared
destination.** AIS message 5 carries it, the generator emitted no message 5, and
the live connector dropped every message that was not a position report. A
comparison against a column that does not exist is a detector that can never
fire, so the column came first (`schemas.records.VoyageDeclaration`,
`ingest.aisstream._parse_static`) and the rules are here.

Two checks, and the split is the same one `anomaly.identity` makes, for the same
reason.

**The arithmetic one: an impossible arrival.** A vessel 900 km from the port she
names, declaring an arrival in six hours, would have to make 81 knots. That is
not a judgement about her intentions; it is a statement about her hull. It has
no false positives that are not data errors, and like the IMO check digit it
either passes or it does not.

Two things are deliberately not checked, and both would fire on most of an
honest fleet. An ETA *later* than she could achieve is slack — traffic,
weather, a berth that is not free — and reads as prudence rather than deceit.
An ETA that has already *passed* is a stale declaration on a late vessel, which
is the commonest thing at sea; the question the brief asks is forward-looking,
and once the stated time is behind her the declaration has stopped asking it.

**The behavioural one: steaming away from where she says she is going.** Softer
by construction and gated accordingly. A vessel may legitimately be diverted
mid-passage, so a declaration that stops matching is ordinary; what is not
ordinary is a declaration that **never** matched. So the test is not "did she
arrive where she said" but "was she ever heading there at all" — sustained
motion away from the declared port, measured from the moment she declared it.

**What is deliberately not built.** Declared destination against *historical*
destination is not a trigger here, and the reason is the Z1 lesson written down
in ADR-030: an unqualified "she has never been there before" fires on every
vessel's first call at every port, which on this corpus is 168 hulls. A
first-ever destination is a fact about our observation window, not about the
ship. History is available to the assistant as context and is not a rule.
"""
from __future__ import annotations

import math
from typing import Optional

from ..ports import PORTS

__all__ = ["VoyageFinding", "resolve_destination", "check_arrival_feasible",
           "check_heading_agrees", "check_voyage", "MAX_HULL_SPEED_KN"]


#: The fastest a merchant hull goes, with margin. A container ship on a schedule
#: makes 24 knots; nothing in this AOI's traffic makes 30. Used as the ceiling
#: for "could she possibly arrive by then", so the check accuses only a vessel
#: whose declaration is impossible for *any* ship, not merely fast for hers.
#:
#: Using her own class speed would catch more and would also start accusing
#: hulls of being slower than they are — a laden VLCC declaring a container
#: ship's transit time is odd; a hull that made the passage is not lying about
#: it. Under ADR-004 the ceiling goes where no honest vessel can be caught.
MAX_HULL_SPEED_KN = 30.0

#: How far off the great-circle a real passage runs: coastlines, traffic
#: separation, weather routing. A straight line understates every real distance,
#: so the feasibility check inflates it before deciding something is impossible.
#: 1.15 is conservative in the direction that avoids false accusations.
ROUTE_SLACK = 1.15

#: Below this the declaration and the track are about the same place and there
#: is nothing to check — she is already there.
MIN_DISTANCE_KM = 20.0

#: How many hours short the declaration has to be before it is a finding.
#:
#: **Without this the check fires on the whole honest fleet, and it did — 43
#: alerts on 41 innocent hulls.** The reason is arithmetic and would happen on
#: any real feed: required speed is distance over *remaining* time, and as the
#: remaining time goes to zero the required speed goes to infinity. A vessel 60
#: km from her berth with half an hour left on a two-day-old ETA "needs 200
#: knots". She is not lying. She is late, which is the commonest thing at sea,
#: and she is still broadcasting the ETA she typed in when she sailed because
#: nobody retypes it.
#:
#: So the test is not "is the required speed impossible" but "is she short by
#: an amount no schedule slips by". Six hours: a tide missed, a pilot delayed,
#: a berth not free are all inside that, and a declaration short by more than
#: half a day was not a plan that went wrong.
#:
#: Measured on the corpus: 43 -> 2, and the two that remain are the two hulls
#: written to lie.
MIN_SHORTFALL_H = 6.0

#: A course this far off the bearing to the declared port counts as "not
#: heading there". Wide on purpose: a vessel rounding a headland, standing off a
#: traffic lane, or beating into weather is not going in a straight line, and
#: the rule has to survive all three.
AWAY_BEARING_DEG = 100.0

#: And she has to keep doing it. A single fix pointing the wrong way is a turn.
MIN_AWAY_HOURS = 6.0

#: Fraction of the observed fixes that must be heading away before the
#: declaration is called contradicted.
AWAY_FRACTION = 0.8

#: Mean speed over the window below which the heading check does not apply.
#:
#: **A vessel at anchor is not steaming away from anywhere, and without this the
#: check said she was.** Measured on the corpus: eleven honest hulls fired,
#: every one of them swinging on her cable in an anchorage while still
#: broadcasting the destination she was waiting to enter. A ship at anchor
#: yaws through most of the compass over a tide, so *every* step is "more than
#: 100 degrees off the bearing to the port" and the away fraction comes out at
#: 1.0 — a perfect score on a question that should never have been asked.
#:
#: Three knots is the same "is she making way" line `tracks.interactions` draws
#: for company and shadowing, and for the same reason: a rule about direction
#: needs a vessel that has one.
UNDERWAY_MIN_KN = 3.0


class VoyageFinding:
    """One statement about a declared voyage, with its verdict.

    Three-valued like :class:`~maritime_isr.anomaly.identity.IdentityFinding`
    and for the same reason: "we could not check" is an answer, and collapsing
    it into "fine" is how a detector reports a clean picture it never looked at.
    """

    __slots__ = ("check", "outcome", "confidence", "statement", "detail")

    def __init__(self, check: str, outcome: str, confidence: float,
                 statement: str, detail: dict | None = None):
        self.check = check
        #: ``contradiction`` | ``ok`` | ``not_checkable``
        self.outcome = outcome
        self.confidence = confidence
        self.statement = statement
        self.detail = detail or {}

    @property
    def is_contradiction(self) -> bool:
        return self.outcome == "contradiction"

    def as_dict(self) -> dict:
        return {"check": self.check, "outcome": self.outcome,
                "confidence": round(self.confidence, 3),
                "statement": self.statement, **self.detail}

    def __repr__(self) -> str:                                # pragma: no cover
        return f"<VoyageFinding {self.check} {self.outcome}>"


# --------------------------------------------------------------------------
# resolving free text to a place
# --------------------------------------------------------------------------

#: Things transmitters put in the destination field that name a place we hold
#: under a different string. Real AIS destination text is a mess — abbreviations,
#: UN/LOCODEs, route notation, the previous voyage left in place — and this table
#: is deliberately small: every entry is a claim that two strings mean one port,
#: and a wrong entry sends a finding to the wrong place.
DESTINATION_ALIASES: dict[str, str] = {
    "NHAVA SHEVA": "JNPT",
    "NHAVASHEVA": "JNPT",
    "INNSA": "JNPT",
    "JAWAHARLAL NEHRU": "JNPT",
    "INBOM": "Mumbai",
    "BOMBAY": "Mumbai",
    "INIXY": "Kandla",
    "INMUN": "Mundra",
    "INSIK": "Sikka",
    "INVAD": "Vadinar",
    "INNML": "Mangalore",
    "NEW MANGALORE": "Mangalore",
    "INCOK": "Kochi",
    "COCHIN": "Kochi",
    "INMRM": "Mormugao",
    "MARMAGAO": "Mormugao",
    "PKKHI": "Karachi",
    "PKGWD": "Gwadar",
}


def resolve_destination(text: Optional[str]) -> Optional[str]:
    """A gazetteer port name for broadcast destination text, or None.

    **It refuses far more than it resolves, and that is the design.** A wrong
    resolution does not produce a missed finding, it produces a finding against
    the wrong port — the system would tell a watchkeeper a ship is lying because
    we guessed where she meant. So this matches exactly: the port name, a known
    alias, or a leading token of a route string (`"JNPT>SIKKA"` declares JNPT
    first). Anything else returns None and the checks answer `not_checkable`.

    No fuzzy matching, no edit distance. "KANDLA" and "KANDIA" are one typo
    apart and so are "SIKKA" and "SIKKA ANCH", but so are plenty of genuinely
    different places, and this rule's value is that it does not invent.
    """
    if not text:
        return None
    raw = str(text).upper().strip()
    if not raw:
        return None
    # Route notation: "A>B", "A>>B", "A-B", "A VIA B" — the first is the next
    # port, which is the one a track can be checked against.
    for sep in (">>", ">", " VIA ", " FOR ", "-"):
        if sep in raw:
            raw = raw.split(sep)[0].strip()
            break
    raw = raw.strip(" .,/")
    if raw in DESTINATION_ALIASES:
        return DESTINATION_ALIASES[raw]
    for name in PORTS:
        if raw == name.upper():
            return name
    return None


# --------------------------------------------------------------------------
# the arithmetic check
# --------------------------------------------------------------------------

def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _ang_diff(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def check_arrival_feasible(*, lat: float, lon: float, declared_at,
                           destination: Optional[str], eta) -> VoyageFinding:
    """Could any ship get from here to there by then?

    Pure arithmetic over distance, time and a hull-speed ceiling. The only
    judgement in it is `ROUTE_SLACK`, and that judgement is made in the
    direction that avoids accusing an honest vessel.
    """
    port = resolve_destination(destination)
    if port is None:
        return VoyageFinding(
            "declared_eta_feasible", "not_checkable", 0.0,
            f"Destination {destination!r} does not resolve to a port in the "
            f"gazetteer, so there is nowhere to measure the passage to."
            if destination else
            "No destination was declared, so an arrival time cannot be tested.")
    if eta is None or declared_at is None:
        return VoyageFinding(
            "declared_eta_feasible", "not_checkable", 0.0,
            f"She declares {port} but states no arrival time.")

    hours = (eta - declared_at).total_seconds() / 3600.0
    plat, plon = PORTS[port]
    dist_km = _km(lat, lon, plat, plon) * ROUTE_SLACK
    if dist_km < MIN_DISTANCE_KM:
        return VoyageFinding(
            "declared_eta_feasible", "not_checkable", 0.0,
            f"She is already within {dist_km:.0f} km of {port}; an arrival "
            f"time says nothing at this range.")

    # **An expired ETA is not checked at all.** The brief asks for declared
    # arrival against *plausible* arrival "given current position and speed",
    # which is a forward-looking question, and once the stated time has passed
    # the declaration has stopped making a claim about the future. What it
    # describes then is a vessel running late — the commonest thing at sea, and
    # made commoner by the fact that nobody retypes an ETA once it slips. Left
    # in, this branch reported every late arrival in the corpus as a
    # contradiction.
    if hours <= 0:
        return VoyageFinding(
            "declared_eta_feasible", "not_checkable", 0.0,
            f"Her declared arrival at {port} passed {abs(hours):.0f} hours ago "
            f"and she is still {dist_km:.0f} km off. That is a stale ETA on a "
            f"late vessel, not a claim about a passage she could not make.",
            dict(port=port, distance_km=round(dist_km, 1),
                 declared_hours=round(hours, 2)))

    # The soonest any hull could be there, and how far short the declaration
    # falls. Measured in **hours**, not as a speed ratio: near arrival the
    # required speed diverges and says nothing (see `MIN_SHORTFALL_H`).
    soonest_h = (dist_km / 1.852) / MAX_HULL_SPEED_KN
    shortfall_h = soonest_h - hours

    if shortfall_h < MIN_SHORTFALL_H:
        late = " She is running late against it, which is not a finding." \
            if hours < soonest_h else ""
        return VoyageFinding(
            "declared_eta_feasible", "ok", 0.0,
            f"{port} is {dist_km:.0f} km off, which no hull covers in under "
            f"{soonest_h:.1f} h; she declares {hours:.1f} h.{late}",
            dict(port=port, distance_km=round(dist_km, 1),
                 soonest_hours=round(soonest_h, 2),
                 shortfall_hours=round(shortfall_h, 2)))

    # Confidence scales with the size of the shortfall. Half a day short is a
    # mistyped date; a week short is a statement nobody could make in good
    # faith. Capped so this never reads as certainty about intent — it is
    # certainty about arithmetic.
    conf = min(0.9, 0.5 + 0.02 * (shortfall_h - MIN_SHORTFALL_H))
    return VoyageFinding(
        "declared_eta_feasible", "contradiction", conf,
        f"She declares {port} in {hours:.0f} hours from {dist_km:.0f} km away. "
        f"The fastest "
        f"hull in this traffic needs {soonest_h:.0f} hours for that passage, "
        f"so the declaration is {shortfall_h:.0f} hours short of possible.",
        dict(port=port, distance_km=round(dist_km, 1),
             declared_hours=round(hours, 2),
             soonest_hours=round(soonest_h, 2),
             shortfall_hours=round(shortfall_h, 2)))


# --------------------------------------------------------------------------
# the behavioural check
# --------------------------------------------------------------------------

def check_heading_agrees(*, destination: Optional[str], fixes) -> VoyageFinding:
    """Was she ever heading towards the port she named?

    `fixes` is a sequence of ``(t, lat, lon)`` from the declaration onward, in
    order. The test is deliberately "never" rather than "no longer": a vessel
    diverted mid-passage is ordinary and a vessel that never pointed at her
    declared port is not.
    """
    port = resolve_destination(destination)
    if port is None:
        return VoyageFinding(
            "declared_destination_agrees", "not_checkable", 0.0,
            f"Destination {destination!r} does not resolve to a port in the "
            f"gazetteer." if destination else "No destination was declared.")
    pts = [f for f in fixes if f is not None]
    if len(pts) < 3:
        return VoyageFinding(
            "declared_destination_agrees", "not_checkable", 0.0,
            f"Only {len(pts)} fix(es) after she declared {port} — not enough "
            f"track to say which way she went.")

    span_h = (pts[-1][0] - pts[0][0]) / 3600.0
    if span_h < MIN_AWAY_HOURS:
        return VoyageFinding(
            "declared_destination_agrees", "not_checkable", 0.0,
            f"Only {span_h:.1f} h of track after she declared {port}; a course "
            f"needs {MIN_AWAY_HOURS:.0f} h to be a direction rather than a turn.")

    plat, plon = PORTS[port]

    # Was she going anywhere at all? A heading is only a claim about direction
    # if the vessel has one. See `UNDERWAY_MIN_KN`.
    travelled_km = sum(_km(a[1], a[2], b[1], b[2]) for a, b in zip(pts, pts[1:]))
    mean_kn = (travelled_km / 1.852) / span_h
    if mean_kn < UNDERWAY_MIN_KN:
        return VoyageFinding(
            "declared_destination_agrees", "not_checkable", 0.0,
            f"She averaged {mean_kn:.1f} kn over the {span_h:.0f} h after "
            f"declaring {port} — waiting, not on passage. Which way she was "
            f"pointing says nothing about where she was going.",
            dict(port=port, mean_kn=round(mean_kn, 2)))

    away = 0
    considered = 0
    for (_, la, lo), (_, la2, lo2) in zip(pts, pts[1:]):
        step = _km(la, lo, la2, lo2)
        if step < 0.3:                     # stopped: she is not going anywhere
            continue
        considered += 1
        made_good = _bearing(la, lo, la2, lo2)
        to_port = _bearing(la, lo, plat, plon)
        if _ang_diff(made_good, to_port) > AWAY_BEARING_DEG:
            away += 1

    if considered < 3:
        return VoyageFinding(
            "declared_destination_agrees", "not_checkable", 0.0,
            f"She barely moved after declaring {port}; there is no course to "
            f"compare.")

    frac = away / considered
    closed = _km(pts[0][1], pts[0][2], plat, plon) - _km(pts[-1][1], pts[-1][2],
                                                         plat, plon)
    if frac < AWAY_FRACTION or closed > 0:
        return VoyageFinding(
            "declared_destination_agrees", "ok", 0.0,
            f"{(1 - frac):.0%} of her run after declaring {port} was towards "
            f"it, closing {closed:.0f} km.",
            dict(port=port, away_fraction=round(frac, 3)))

    return VoyageFinding(
        "declared_destination_agrees", "contradiction", 0.55,
        f"She declared {port} and then steamed away from it for {span_h:.0f} "
        f"hours — {frac:.0%} of her run was on a course more than "
        f"{AWAY_BEARING_DEG:.0f}° off the bearing to it, and she ended "
        f"{abs(closed):.0f} km further away than she started. This is not a "
        f"diversion; she was never heading there.",
        dict(port=port, away_fraction=round(frac, 3),
             opened_km=round(abs(closed), 1), hours=round(span_h, 1)))


def check_voyage(*, lat: float, lon: float, declared_at, destination, eta,
                 fixes=()) -> list[VoyageFinding]:
    """Both checks over one declaration. Order is cheapest-and-surest first."""
    return [
        check_arrival_feasible(lat=lat, lon=lon, declared_at=declared_at,
                               destination=destination, eta=eta),
        check_heading_agrees(destination=destination, fixes=fixes),
    ]
