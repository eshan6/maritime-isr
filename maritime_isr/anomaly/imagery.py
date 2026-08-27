"""Does what the camera sees match what the transponder claims? — Area 5.

*"Mismatch alerting, which is the payoff. The camera says one thing, the
transponder says another. A vessel declaring itself a fishing vessel that images
as a tanker is a strong, legible, immediately actionable finding."* — the IDEX
Challenge 82 brief, Area 5.

A pure function over a declared class and an image verdict, three-valued like
:mod:`.identity`, :mod:`.voyage` and :mod:`.paperwork`: ``contradiction`` /
``ok`` / ``not_checkable``. "We could not tell" is an answer, and folding it into
"fine" would report a fleet checked by a camera that never resolved anything.

Five ways this rule refuses, and each one is a false positive that would
otherwise happen
-----------------------------------------------------------------------------
**1. She declared nothing.** A hull broadcasting no static type has not lied
about it. Most radar contacts are in this state, which is exactly why the
imagery on *them* is evidence rather than a contradiction.

**2. The image was not good enough.** A hull at 19 km through monsoon haze
produces a picture, not evidence. The classifier's own quality floor
(:data:`eo.classify.MIN_CLASSIFY_QUALITY`) does most of this work and the rule
declines again above it, because naming a type for an operator's information and
accusing a named ship of misdeclaring herself are different standards of proof.

**3. The image could not have supported the distinction.** The comparison
happens in the coarse vocabulary the classifier could actually resolve **under
this capture's own conditions**, which is narrower at night than in daylight: a
thermal silhouette does not show a deck, and the deck is the only thing that
separates a tanker from a bulker. Comparing a declared class against a label the
image could not have carried is how a demo produces impressive numbers and an
operator loses a morning.

**4. The difference is one two honest sources make.** This is the important one,
and it is not the same as (3). A hull declaring ``general_cargo`` that images
unmistakably as a bulker has contradicted nothing — both are cargo, the image
*can* tell them apart, and ADR-034 already established the principle for the
registry check: *"two registries disagreeing about one hull, not a lie"*. So the
final comparison is at the level of the **AIS ship-type family** — fishing,
cargo, tanker, passenger, military — which is not a vocabulary this project
invented but the grouping the standard itself uses (ITU-R M.1371 allocates 30 to
fishing, 70-79 to cargo, 80-89 to tanker). Within a family, no contradiction;
across families, a hull is claiming to be a different kind of ship from the one
in the photograph.

**5. The declared class, or the imaged one, has no family.** A label this model
holds no reference for, or a coarse label that *spans* families — "merchant"
covers both tanker and cargo — supports no comparison. Saying so is not the same
as saying the declaration is fine.

**What is deliberately not a rule here.** A capture whose frame was empty —
the camera slewed onto a bearing and there was nothing there — is not turned
into an alert, even though it is a legible finding about a track. The reason is
in ADR-037: the simulated camera never misses a target that is present, and a
real one does, so the rule would be calibrated against a false-negative rate
this project does not have. The empty frame is recorded on the capture and
counted; promoting it needs a real camera first.
"""
from __future__ import annotations

from typing import Optional

# The AIS ship-type families live in `eo.classify` rather than here, and the
# dependency runs that way round for a reason: the *classifier* needs them too.
# A model that cannot reliably place a hull in a family must not publish a
# family-level label, so the vocabulary merge is measured against these groups —
# see `eo.classify.FAMILY_SEPARATION_THRESHOLD`. Re-exported so a reader of this
# rule can find the taxonomy it is applying.
from ..eo.classify import (AIS_TYPE_FAMILY, families_of_imaged,
                           family_of_declared)

__all__ = ["ImageryFinding", "check_declared_type", "CONTRADICTION", "OK",
           "NOT_CHECKABLE", "MIN_MISMATCH_QUALITY", "MIN_MISMATCH_CONFIDENCE",
           "AIS_TYPE_FAMILY", "family_of_declared", "families_of_imaged"]

CONTRADICTION = "contradiction"
OK = "ok"
NOT_CHECKABLE = "not_checkable"

#: Image quality below which no accusation is made, above the classifier's own
#: floor of 0.35.
#:
#: **Two floors, on purpose, and they mean different things.** The classifier
#: refuses below 0.35 because there is not enough picture to name a type at all.
#: This rule refuses below 0.45 because naming a type for an operator's
#: information and contradicting a vessel's declared identity are different
#: standards of proof — the second one puts a ship on a queue, and ADR-004 spends
#: its whole budget keeping that queue worth opening.
MIN_MISMATCH_QUALITY = 0.45

#: Classifier confidence below which no accusation is made. Above the
#: classifier's own 0.50 bar, for the same reason.
MIN_MISMATCH_CONFIDENCE = 0.62


class ImageryFinding:
    """One statement about an image against a declaration, with its verdict."""

    __slots__ = ("check", "outcome", "confidence", "statement", "detail")

    def __init__(self, check: str, outcome: str, confidence: float,
                 statement: str, detail: Optional[dict] = None):
        self.check = check
        #: ``contradiction`` | ``ok`` | ``not_checkable``
        self.outcome = outcome
        self.confidence = confidence
        self.statement = statement
        self.detail = detail or {}

    @property
    def is_contradiction(self) -> bool:
        return self.outcome == CONTRADICTION

    def as_dict(self) -> dict:
        return {"check": self.check, "outcome": self.outcome,
                "confidence": round(self.confidence, 3),
                "statement": self.statement, **self.detail}

    def __repr__(self) -> str:                                # pragma: no cover
        return f"<ImageryFinding {self.check} {self.outcome}>"


def _pretty(label: Optional[str]) -> str:
    return str(label or "").replace("_", " ")


def check_declared_type(*, declared_class: Optional[str], verdict,
                        quality: float, band: Optional[str] = None
                        ) -> ImageryFinding:
    """The camera's type against the declared type, in the image's own vocabulary.

    ``verdict`` is an :class:`eo.classify.ImageVerdict`. ``quality`` and ``band``
    describe the look — they decide which distinctions the image could have
    supported, and the declared class is mapped through the *same* vocabulary so
    that like is compared with like.
    """
    from ..eo.classify import coarse_at

    check = "declared_type_against_image"
    band = band or getattr(verdict, "band", "visible")
    detail = {"declared_class": declared_class,
              "imaged_type": getattr(verdict, "imaged_type", None),
              "image_quality": round(float(quality), 3),
              "band": band}

    if verdict is None or not getattr(verdict, "is_claim", False):
        why = (getattr(verdict, "not_classifiable", "") if verdict
               else "no classifier verdict on this capture")
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"The image supports no type claim, so there is nothing to compare "
            f"against what she declares ({why}).", detail)

    if not declared_class:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            "She broadcasts no vessel type, so the image cannot contradict "
            "one. The image is still evidence about her; it is not a "
            "contradiction.", detail)

    if float(quality) < MIN_MISMATCH_QUALITY:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"Image quality {float(quality):.2f} is below the "
            f"{MIN_MISMATCH_QUALITY:.2f} needed to contradict a declared "
            f"identity. The type claim stands as information; it is not "
            f"grounds for an alert.", detail)

    imaged = verdict.imaged_type
    declared_group = coarse_at(declared_class, quality=quality, band=band)
    detail["declared_group"] = declared_group

    # (3) The image resolved her declared class and the image agrees with it.
    if declared_group is not None and declared_group == imaged:
        return ImageryFinding(
            check, OK, min(0.9, float(verdict.confidence)),
            f"She declares {_pretty(declared_class)} and images as "
            f"{_pretty(imaged)}, which is what {_pretty(declared_class)} looks "
            f"like in the {band} band. The declaration and the picture agree.",
            detail)

    # (5) Families, which is the level a declaration is answerable at.
    d_family = family_of_declared(declared_class)
    # **The model's own reading of its own label, wherever it supplies one.**
    # Re-deriving it here against the default vocabulary is how a swapped-in
    # classifier's coarse labels got over-read into precise family claims and
    # accused a third of an honest fleet — see `eo.classify.families_of_imaged`.
    # The fallback stands for a third-party verdict that declines to say.
    i_families = getattr(verdict, "imaged_families", None)
    if i_families is None:
        i_families = families_of_imaged(imaged, quality=quality, band=band)
    detail["declared_family"] = d_family
    detail["imaged_families"] = sorted(i_families) if i_families else None
    if d_family is None:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"She declares {_pretty(declared_class)}, which the AIS ship-type "
            f"standard does not cleanly place in a family, so the image "
            f"supports no comparison.", detail)
    if i_families is None:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"The image supports only “{_pretty(imaged)}”, which rules out no "
            f"AIS ship-type family, so it cannot contradict a declared "
            f"{_pretty(declared_class)}. In the {band} band at quality "
            f"{float(quality):.2f} this model cannot resolve her far enough.",
            detail)

    # (4) The image leaves her declared family open. Either it agrees, or it
    # cannot separate her family from another and has ruled nothing out — and
    # in both cases she has contradicted nothing. This is where a hull
    # declaring `bulker` that images unmistakably as a general cargo ship goes
    # quiet: both are cargo, and two sources classifying one hull differently
    # inside a family is routine (ADR-034's `class_quibble`).
    if d_family in i_families:
        spans = (f" The image narrows her to {', '.join(sorted(i_families))}, "
                 f"which includes what she declares."
                 if len(i_families) > 1 else "")
        return ImageryFinding(
            check, OK, min(0.9, float(verdict.confidence)),
            f"She declares {_pretty(declared_class)} and images as "
            f"{_pretty(imaged)}. Both are {d_family} under the AIS ship-type "
            f"standard.{spans}", detail)

    # (2) Strong enough to accuse?
    if float(verdict.confidence) < MIN_MISMATCH_CONFIDENCE:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"The image reads as {_pretty(imaged)} at "
            f"{float(verdict.confidence):.2f}, below the "
            f"{MIN_MISMATCH_CONFIDENCE:.2f} needed to contradict a declared "
            f"{_pretty(declared_class)}.", detail)

    # A contradiction. Confidence rides on the classifier's, scaled by how good
    # the look was — a confident label off a mediocre picture is a weaker
    # accusation than the same label off a good one, and the number has to say
    # so rather than inheriting the classifier's certainty wholesale.
    conf = min(0.95, float(verdict.confidence) * (0.6 + 0.4 * float(quality)))
    return ImageryFinding(
        check, CONTRADICTION, conf,
        f"She broadcasts that she is a {_pretty(declared_class)}, which is a "
        f"{d_family} under the AIS ship-type standard. The camera images her "
        f"as a {_pretty(imaged)}, which this model can place in "
        f"{' or '.join(sorted(i_families))} and nothing else, at confidence "
        f"{float(verdict.confidence):.2f} in the {band} band. A hull does not "
        f"change shape between messages.",
        detail)
