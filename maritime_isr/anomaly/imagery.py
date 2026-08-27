"""Does what the camera sees match what the transponder claims? — Area 5.

*"Mismatch alerting, which is the payoff. The camera says one thing, the
transponder says another. A vessel declaring itself a fishing vessel that images
as a tanker is a strong, legible, immediately actionable finding."* — the IDEX
Challenge 82 brief, Area 5.

A pure function over a declared class and an image verdict, three-valued like
:mod:`.identity`, :mod:`.voyage` and :mod:`.paperwork`: ``contradiction`` /
``ok`` / ``not_checkable``. "We could not tell" is an answer, and folding it into
"fine" would report a fleet checked by a camera that never resolved anything.

Four ways this rule refuses, and each one is a false positive that would
otherwise happen
-----------------------------------------------------------------------------
**1. The image was not good enough.** A hull at 19 km through monsoon haze
produces a picture, not evidence. The classifier's own quality floor
(:data:`eo.classify.MIN_CLASSIFY_QUALITY`) does most of this work and the rule
declines again above it, because a marginal claim is the wrong thing to accuse
somebody with.

**2. The two classes are not separable in this image.** This is the important
one. A hull declaring `bulker` that images as dry cargo has not contradicted
anything — a bulker *is* dry cargo. So the comparison happens in the coarse
vocabulary the classifier could actually resolve **under this capture's own
conditions**, which is narrower at night than in daylight (a thermal silhouette
does not show a deck, and the deck is what separates a tanker from a bulker).
Comparing a declared class against a label the image could not have supported is
how a demo produces impressive numbers and an operator loses a morning.

**3. The declared class is one the model has no prototype for.** No comparison
exists; saying so is not the same as saying the declaration is fine.

**4. She declared nothing.** A hull broadcasting no static type has not lied
about it. Most radar contacts are in this state, which is exactly why the
imagery on *them* is evidence rather than a contradiction.

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

__all__ = ["ImageryFinding", "check_declared_type", "CONTRADICTION", "OK",
           "NOT_CHECKABLE", "MIN_MISMATCH_QUALITY", "MIN_MISMATCH_CONFIDENCE"]

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

    declared_group = coarse_at(declared_class, quality=quality, band=band)
    detail["declared_group"] = declared_group
    if declared_group is None:
        return ImageryFinding(
            check, NOT_CHECKABLE, 0.0,
            f"She declares {declared_class!r}, which this model holds no "
            f"reference for, so no comparison is possible.", detail)

    imaged = verdict.imaged_type
    if declared_group == imaged:
        return ImageryFinding(
            check, OK, min(0.9, float(verdict.confidence)),
            f"She declares {_pretty(declared_class)} and images as "
            f"{_pretty(imaged)}, which is what {_pretty(declared_class)} looks "
            f"like in this band. The declaration and the picture agree.",
            detail)

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
        f"She broadcasts that she is a {_pretty(declared_class)}. The camera "
        f"images her as a {_pretty(imaged)} at confidence "
        f"{float(verdict.confidence):.2f} in the {band} band, and the two are "
        f"distinguishable in an image of this quality. A hull does not change "
        f"shape between messages.",
        detail)
