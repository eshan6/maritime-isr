"""The factor registry: what kinds of suspicion exist, what each is worth, and
which of the six capability areas it comes from.

**One place, deliberately.** A factor kind needs a weight (for the score), a
family (for the operator's mental model), a home area (so the build can show the
list gaining classes as areas land), a plain-English name and a set of candidate
next actions. Scattering those across five modules is how a system ends up with
a factor that scores but never narrates, or narrates but proposes nothing. Adding
a kind is one dict entry here plus a collector in :mod:`.collect`.

**The weights are policy and they are visible.** Same posture as
``anomaly.risk.ANOMALY_WEIGHTS`` and ``config.ANOMALY_THRESHOLDS``: there is no
learned black box, because an unexplainable score is unsellable to a navy and to
an insurer alike. A weight is "how much does this kind of fact, at full
confidence, move a subject up the queue", in [0,1].

**Three of the six families are declared and empty.** ``paperwork``, ``imagery``
and ``radio`` are Areas 4, 5 and 6 of the Section-3 brief and nothing produces
them yet. They are listed here rather than omitted so the product can state the
hole rather than imply completeness — :func:`family_coverage` reports which
families a given picture actually contains, which is the honest version of a
progress bar.
"""
from __future__ import annotations

from typing import Iterable, Optional

__all__ = ["FAMILIES", "FACTOR_KINDS", "spec", "weight_of", "family_of",
           "area_of", "known_kinds", "family_coverage", "FactorSpec"]


#: The six families a factor can belong to, in the order an operator reads them.
#: Keyed to the Section-3 brief's areas so "the ranked list gained a new class of
#: factor" is checkable rather than a claim.
FAMILIES: dict[str, dict] = {
    "motion": dict(
        label="Motion and behaviour",
        blurb="what the vessel is doing, from its track — radar or AIS alike",
        areas=("Area 2 (predictive AIS)", "Area 3 (radar classification)")),
    "identity": dict(
        label="Declared identity",
        blurb="whether what the vessel says about itself holds together",
        areas=("Area 2 (predictive AIS)",)),
    "network": dict(
        label="Ownership and designation",
        blurb="who controls this hull and who has been designated",
        areas=("existing graph layer",)),
    "paperwork": dict(
        label="Arrival notifications",
        blurb="what the paperwork declares against what the track shows",
        areas=("Area 4 (PANS / arrival notifications)",)),
    "imagery": dict(
        label="Electro-optical",
        blurb="what a camera saw against what the transponder claims",
        areas=("Area 5 (EO loop)",)),
    "radio": dict(
        label="Radio traffic",
        blurb="what was said on VHF against what the track shows",
        areas=("Area 6 (VHF ASR/NLP)",)),
}


#: How a second instance of the same kind on the same subject combines.
#:
#: **This distinction is not pedantry — getting it wrong overstates confidence,
#: and it did.** Measured on the first build of this module: 19 hulls reached
#: 0.97 confidence on ``sanctioned_ownership`` because the fact arrived twice —
#: once from the landed sanctions match table, once from walking the graph's
#: ownership chain — and was combined as two independent observations. It is one
#: designation seen from two angles. Combining it that way makes a system sound
#: more certain the more places it looks, which is precisely backwards.
#:
#:   * ``occurrences`` — genuinely separate events. Four loitering episodes are
#:     four things that happened; each one raises the claim. Noisy-OR.
#:   * ``restatement`` — one standing fact, re-derived. A hull is designated, or
#:     flies Panama, or has changed name twice; asserting it from a second
#:     source corroborates but does not accumulate. Take the maximum.
REPEAT_OCCURRENCES = "occurrences"
REPEAT_RESTATEMENT = "restatement"


class FactorSpec:
    """One registered kind of suspicion."""

    __slots__ = ("kind", "family", "area", "weight", "label", "blurb",
                 "actions", "attribution", "repeats")

    def __init__(self, kind: str, *, family: str, area: str, weight: float,
                 label: str, blurb: str, actions: tuple[str, ...],
                 attribution: str = "maritime-isr",
                 repeats: str = REPEAT_OCCURRENCES):
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r} for factor {kind!r}; "
                             f"expected one of {sorted(FAMILIES)}")
        if not 0.0 < weight <= 1.0:
            raise ValueError(f"factor {kind!r} weight {weight} must be in (0,1]")
        if repeats not in (REPEAT_OCCURRENCES, REPEAT_RESTATEMENT):
            raise ValueError(f"factor {kind!r}: unknown repeat semantics "
                             f"{repeats!r}")
        self.kind = kind
        self.family = family
        self.area = area
        self.weight = weight
        self.label = label
        self.blurb = blurb
        self.actions = actions
        #: Who asserted the underlying fact. Not always us — a GFW gap
        #: assessment and an OFAC designation are other people's findings that
        #: we carry, and the sentence has to say so wherever it appears.
        self.attribution = attribution
        self.repeats = repeats


def _s(kind: str, **kw) -> tuple[str, FactorSpec]:
    return kind, FactorSpec(kind, **kw)


#: Every factor kind the assistant knows how to rank, narrate and act on.
#:
#: Weights are relative judgements, stated so they can be argued with:
#:
#:   * A hull designated under a sanctions programme, matched on IMO, is the
#:     strongest single fact available — an IMO number survives renaming and
#:     reflagging, and the designation is a government's decision, not ours.
#:   * A contact on radar with nothing broadcasting there is the headline
#:     capability of the whole system and scores just below it, held down only
#:     because it is a claim about a sensor picture rather than about a hull.
#:   * A port-risk propagation is the weakest: calling at a port we think poorly
#:     of is a fact about a trade route, not about a ship, and it was already
#:     measured firing on eight ordinary merchants in the Kandla rotation.
FACTOR_KINDS: dict[str, FactorSpec] = dict([
    # ---- motion / behaviour --------------------------------------------
    _s("dark_vessel", family="motion", area="ADR-028 coastal radar",
       weight=0.85,
       label="Dark contact",
       blurb="a contact held on radar with nothing broadcasting there",
       actions=("cue_eo_camera", "call_vhf", "check_imaging_opportunity",
                "dispatch_patrol", "monitor")),
    _s("transponder_shutdown", family="motion", area="ADR-029",
       weight=0.9,
       label="Transponder shutdown",
       blurb="a contact that was identified, then stopped broadcasting while "
             "still being tracked",
       actions=("call_vhf", "cue_eo_camera", "compare_own_history",
                "dispatch_patrol", "escalate")),
    _s("dark_rendezvous", family="motion", area="roadmap 5.2",
       weight=0.8,
       label="Meeting with a silent party",
       blurb="sustained close-quarters contact where one party was "
             "unexplained at the time",
       actions=("cue_eo_camera", "call_vhf", "compare_own_history",
                "check_arrival_notification", "monitor")),
    _s("loitering_sensitive", family="motion", area="roadmap 5.2",
       weight=0.6,
       label="Loitering in a sensitive area",
       blurb="sustained low speed inside a watched area, away from any berth "
             "or designated anchorage",
       actions=("cue_eo_camera", "call_vhf", "query_zone_history", "monitor")),
    _s("lane_deviation", family="motion", area="ADR-030 zone layer",
       weight=0.45,
       label="Off the customary route",
       blurb="well outside every shipping corridor for a sustained period "
             "while still making way",
       actions=("call_vhf", "compare_own_history", "monitor")),
    _s("anchored_outside_limits", family="motion", area="ADR-030 zone layer",
       weight=0.5,
       label="Anchored outside port limits",
       blurb="stopped inside territorial waters and outside every declared "
             "facility",
       actions=("call_vhf", "cue_eo_camera", "check_arrival_notification",
                "monitor")),
    _s("maiden_zone_visit", family="motion", area="ADR-030 zone layer",
       weight=0.4,
       label="First visit to this area",
       blurb="a hull we have watched working this coast, now somewhere she has "
             "never been",
       actions=("compare_own_history", "check_arrival_notification",
                "monitor")),
    _s("assessed_ais_disabling", family="motion", area="GFW carried finding",
       weight=0.7,
       label="Assessed AIS disabling",
       blurb="an AIS gap that Global Fishing Watch assessed as deliberate",
       attribution="Global Fishing Watch",
       actions=("check_imaging_opportunity", "compare_own_history",
                "monitor")),

    _s("notable_activity", family="motion", area="Area 2 (predictive AIS)",
       weight=0.5,
       label="Unusual activity",
       blurb="motion that matches a behaviour worth a second look — a survey "
             "pattern, erratic manoeuvring, or drifting",
       actions=("cue_eo_camera", "call_vhf", "compare_own_history",
                "query_zone_history", "monitor")),

    _s("vessel_interaction", family="motion",
       area="Area 3 (radar classification)",
       weight=0.6,
       label="Interaction with another vessel",
       blurb="behaviour in relation to another track — a transfer, one vessel "
             "shadowing another, or two keeping formation",
       actions=("cue_eo_camera", "call_vhf", "compare_own_history",
                "dispatch_patrol", "monitor")),

    # ---- declared identity ---------------------------------------------
    _s("identity_contradiction", family="identity",
       area="Area 2 (predictive AIS)",
       weight=0.75,
       label="Declared identity does not hold together",
       blurb="the identity this hull broadcasts contradicts itself or the "
             "registry — a failed IMO check digit, an MMSI country prefix "
             "that disagrees with the declared flag, or a name or call sign "
             "the registry does not carry",
       actions=("check_registry", "call_vhf", "compare_own_history",
                "escalate"),
       repeats=REPEAT_RESTATEMENT),
    # **`identity`, not `paperwork`, and the distinction is load-bearing.** The
    # `paperwork` family is Area 4 — arrival notifications, a document a port
    # receives — and it is declared empty so `family_coverage` can state the
    # hole. Filing a voyage declaration there would report that family as
    # covered while Area 4 remains unbuilt, which is exactly the overstatement
    # the empty families exist to prevent. A destination broadcast on AIS is
    # something the vessel says about herself, on the air, in the same message
    # stream as her name and her call sign.
    _s("voyage_contradiction", family="identity",
       area="Area 2 (predictive AIS)",
       weight=0.70,
       label="Declared voyage contradicts her own track",
       blurb="what she broadcast about this voyage does not match what she "
             "did — an arrival time no hull could make from where she was, or "
             "a destination she never once steered towards",
       actions=("call_vhf", "check_arrival_notification",
                "compare_own_history", "escalate"),
       repeats=REPEAT_RESTATEMENT),
    _s("ais_spoofing", family="identity", area="roadmap 2.2 / 5.2",
       weight=0.85,
       label="Identity contradiction on the air",
       blurb="two hulls under one MMSI, or a position jump no ship could make",
       actions=("call_vhf", "check_registry", "cue_eo_camera", "escalate")),
    _s("identity_then_anomaly", family="identity", area="roadmap 5.2",
       weight=0.9,
       label="Identity change then dark behaviour",
       blurb="a rename, reflag or MMSI swap followed within days by going "
             "dark — the laundering sequence",
       actions=("check_registry", "compare_own_history", "escalate")),
    _s("identity_change", family="identity", area="Phase 4 graph",
       weight=0.35,
       label="Identity changed on record",
       blurb="the hull is recorded under more than one name, flag or MMSI over "
             "time",
       actions=("check_registry", "compare_own_history"),
       repeats=REPEAT_RESTATEMENT),
    _s("flag_opacity", family="identity", area="roadmap 5.3",
       weight=0.3,
       label="Flag opacity",
       blurb="flies a flag of convenience, or has reflagged on record",
       actions=("check_registry",),
       repeats=REPEAT_RESTATEMENT),

    # ---- ownership and designation --------------------------------------
    _s("sanctions_designation", family="network", area="ADR-018",
       weight=0.95,
       label="Sanctions designation",
       blurb="matches a vessel designated by a sanctions authority",
       attribution="OFAC / UN / EU (designation); the identity match is ours",
       actions=("check_registry", "escalate", "monitor"),
       repeats=REPEAT_RESTATEMENT),
    _s("sanctioned_ownership", family="network", area="roadmap 5.3",
       weight=0.7,
       label="Sanctioned ownership chain",
       blurb="owned or operated, through the ownership chain, by a designated "
             "entity",
       attribution="OFAC / UN / EU (designation); the chain is ours",
       actions=("check_registry", "escalate"),
       repeats=REPEAT_RESTATEMENT),
    _s("port_risk_propagation", family="network", area="roadmap 5.2",
       weight=0.25,
       label="High-risk port calls",
       blurb="called at ports this system carries a risk weight for",
       actions=("check_arrival_notification", "compare_own_history"),
       repeats=REPEAT_RESTATEMENT),
])


def known_kinds() -> tuple[str, ...]:
    return tuple(FACTOR_KINDS)


def spec(kind: str) -> FactorSpec:
    try:
        return FACTOR_KINDS[kind]
    except KeyError:
        raise KeyError(
            f"unregistered factor kind {kind!r}. Register it in "
            f"assistant/catalog.py — a kind with no entry has no weight, no "
            f"family and no proposed action, so it would score as nothing and "
            f"narrate as nothing while still appearing to work."
        ) from None


def weight_of(kind: str) -> float:
    return spec(kind).weight


def family_of(kind: str) -> str:
    return spec(kind).family


def area_of(kind: str) -> str:
    return spec(kind).area


def family_coverage(kinds: Iterable[str]) -> list[dict]:
    """Which families this picture actually contains, and which are empty.

    The empty ones are the point. Three of the six areas in the Section-3 brief
    produce no factor yet, and a surface that simply does not show them looks
    identical to one where they found nothing.
    """
    present = {family_of(k) for k in kinds if k in FACTOR_KINDS}
    out = []
    for name, meta in FAMILIES.items():
        out.append({
            "family": name,
            "label": meta["label"],
            "blurb": meta["blurb"],
            "areas": list(meta["areas"]),
            "present": name in present,
            "kinds": sorted(k for k, s in FACTOR_KINDS.items()
                            if s.family == name),
        })
    return out


def describe(kind: str) -> Optional[dict]:
    s = FACTOR_KINDS.get(kind)
    if s is None:
        return None
    return {"kind": s.kind, "family": s.family, "area": s.area,
            "weight": s.weight, "label": s.label, "blurb": s.blurb,
            "attribution": s.attribution, "actions": list(s.actions)}
