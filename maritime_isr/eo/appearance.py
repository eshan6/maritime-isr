"""What a hull looks like, as six numbers — and why this file exists at all.

**There are no pixels in this system.** Nothing here has ever opened an image.
What this module defines is the *interface between an image and a classifier*:
the small set of measurements a vision model would extract from a photograph of
a ship, expressed as a vector so that the rest of Area 5 — the cueing, the
tagging, the library, the mismatch rule — can be built, exercised and measured
end to end without one.

Saying that plainly is the point. The brief calls image classification "the
commodity part of this problem — the part any competitor can also do", and the
instruction that follows is to build the loop and treat the classifier as
replaceable. A descriptor is what makes that replaceable *interface* concrete:
:mod:`.classify` consumes descriptors, not JPEGs, so a customer's own model
drops in behind :class:`~.classify.ImageClassifier` by producing the same six
numbers from real imagery.

The six
-------
Chosen because each is (a) legible in a side-on photograph of a ship at a few
hundred pixels, and (b) actually discriminating between hull types:

``length_m``               overall length. The single strongest feature, and
                           the one foreshortening destroys.
``length_beam_ratio``      slenderness. A tanker is long and full; a fishing
                           boat is short and beamy for her length.
``superstructure_position`` 0 forward, 1 aft. Tankers and bulkers carry the
                           accommodation right aft; a trawler's wheelhouse is
                           forward of amidships.
``freeboard_ratio``        freeboard as a fraction of length. Small craft ride
                           high for their size; a laden tanker rides low.
``deck_clutter``           0 for a flush tanker deck, 1 for a deck covered in
                           gear. **This is the feature that separates a tanker
                           from a bulker**, which motion alone never can
                           (ADR-033) — and it is the first thing a thermal
                           image loses, which is why night captures are
                           deliberately weaker claims.
``mast_count``             derricks, cranes, gantries.

**The generator holds these constants and so does nothing else.** A corpus built
from a classifier's own prototypes could not falsify that classifier, so the
scenario's camera simulator derives a hull's appearance from her *dimensions and
her physical class*, while the classifier compares against prototypes declared
here for the classifier's use. `tests/test_area5.py` pins the two apart.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

__all__ = ["Appearance", "HULL_FORMS", "descriptor_for", "observe",
           "distance", "FEATURE_WEIGHTS", "MIN_ASPECT_FOR_LENGTH"]


#: Below this |sin(aspect)| the length measurement cannot be recovered.
#:
#: The camera knows the aspect — the station has the target's course from radar
#: and its own bearing — so it can divide the apparent length back out. That
#: correction divides by |sin(aspect)|, and near bow-on it divides by almost
#: nothing and amplifies the measurement error without bound. 0.30 is about 17°
#: off the bow: past that the honest answer is "this image does not carry her
#: length", and the classifier is built to say so rather than to report a 190 m
#: tanker as a 60 m trawler.
MIN_ASPECT_FOR_LENGTH = 0.30


@dataclass(frozen=True)
class Appearance:
    """The six numbers, plus what the viewing conditions cost this look."""
    length_m: float
    length_beam_ratio: float
    superstructure_position: float
    freeboard_ratio: float
    deck_clutter: float
    mast_count: float
    #: False when the look was too close to bow-on to carry a length.
    length_reliable: bool = True
    #: False on a thermal image: a silhouette does not show deck fittings.
    deck_readable: bool = True

    def as_dict(self) -> dict:
        return {"length_m": round(self.length_m, 1),
                "length_beam_ratio": round(self.length_beam_ratio, 3),
                "superstructure_position": round(self.superstructure_position, 3),
                "freeboard_ratio": round(self.freeboard_ratio, 4),
                "deck_clutter": round(self.deck_clutter, 3),
                "mast_count": round(self.mast_count, 2),
                "length_reliable": self.length_reliable,
                "deck_readable": self.deck_readable}


#: Per physical class: (superstructure position, freeboard ratio, deck clutter,
#: masts). Naval-architecture conventions, not tuning knobs:
#:
#:   * Tankers and bulkers carry the accommodation right aft (0.88-0.90).
#:   * A tanker's deck is flush with piping; a bulker's carries hatch coamings
#:     and often cranes; a general-cargo ship is covered in derricks.
#:   * Freeboard as a fraction of length falls with size — a 330 m VLCC is not
#:     thirty times as tall as an 11 m skiff.
#:   * A trawler's wheelhouse is well forward and her working deck is aft.
HULL_FORMS: dict[str, tuple[float, float, float, float]] = {
    "VLCC":           (0.90, 0.055, 0.10, 1.0),
    "Suezmax":        (0.90, 0.060, 0.10, 1.0),
    "Aframax":        (0.90, 0.062, 0.14, 1.0),
    "product_tanker": (0.88, 0.070, 0.28, 1.0),
    "bulker":         (0.88, 0.085, 0.58, 1.0),
    "general_cargo":  (0.80, 0.090, 0.82, 2.0),
    "container":      (0.74, 0.095, 0.70, 1.0),
    "reefer":         (0.85, 0.095, 0.46, 2.0),
    "fishing":        (0.35, 0.150, 0.95, 2.0),
    "dhow":           (0.55, 0.135, 0.85, 1.0),
    "naval":          (0.45, 0.075, 0.60, 3.0),
}

#: Fallback for a class the table does not carry. Deliberately mid-range and
#: deliberately *not* silently merged into a neighbour: an unknown class should
#: produce an unremarkable descriptor that the classifier will not place
#: confidently, rather than one that happens to look like a tanker.
_DEFAULT_FORM = (0.75, 0.090, 0.55, 1.0)


def descriptor_for(vessel_class: Optional[str], *, length_m: float,
                   beam_m: Optional[float] = None,
                   draught_m: Optional[float] = None) -> Appearance:
    """The appearance of a hull with these dimensions and this physical form.

    ``vessel_class`` here is the hull's **physical** class, not what she
    broadcasts. That distinction is the whole of the mismatch check: a camera
    photographs steel, not a message, so a 180 m tanker declaring herself a
    fishing vessel still looks like a 180 m tanker.
    """
    aft, freeboard, clutter, masts = HULL_FORMS.get(
        str(vessel_class or ""), _DEFAULT_FORM)
    length_m = max(float(length_m or 0.0), 1.0)
    if beam_m:
        ratio = length_m / max(float(beam_m), 0.5)
    else:
        # Beam is not always held. A hull's slenderness scales weakly with her
        # length, so this is a usable stand-in — and it is a stand-in rather
        # than a refusal because a descriptor missing a component would have to
        # be special-cased everywhere downstream.
        ratio = 4.0 + 2.2 * math.log10(max(length_m, 10.0))
    if draught_m:
        # A laden hull sits lower, which raises her apparent freeboard ratio's
        # denominator rather than her numerator: deeper draught, less freeboard.
        freeboard *= max(0.65, 1.25 - 0.45 * float(draught_m) / max(
            0.06 * length_m, 1.0))
    return Appearance(length_m=length_m, length_beam_ratio=ratio,
                      superstructure_position=aft, freeboard_ratio=freeboard,
                      deck_clutter=clutter, mast_count=masts)


#: How much each feature counts, and the span it is scaled by before the
#: weighting. The spans are the range each feature actually occupies across the
#: table above, so no feature dominates the distance merely by having larger
#: units. Weights sum to 1.
FEATURE_WEIGHTS: dict[str, tuple[float, float]] = {
    # feature: (weight, span used to normalise the difference)
    "log_length":     (0.34, 1.20),    # log10 m, ~11 m to ~330 m
    "slenderness":    (0.16, 5.00),
    "superstructure": (0.14, 0.60),
    "freeboard":      (0.10, 0.10),
    "clutter":        (0.20, 0.90),
    "masts":          (0.06, 3.00),
}


def _components(a: Appearance) -> dict[str, float]:
    return {
        "log_length": math.log10(max(a.length_m, 1.0)),
        "slenderness": a.length_beam_ratio,
        "superstructure": a.superstructure_position,
        "freeboard": a.freeboard_ratio,
        "clutter": a.deck_clutter,
        "masts": a.mast_count,
    }


def distance(a: Appearance, b: Appearance) -> float:
    """Weighted distance between two appearances, in [0, ~1].

    Features the *observed* look could not carry are dropped from the comparison
    and the remaining weights are renormalised, rather than being compared
    against a placeholder. A thermal image that cannot see a deck must not be
    scored as if it had seen a flush one — that is the difference between "we
    could not tell" and "she is a tanker", and folding the first into the second
    is the failure the three-valued rules in `anomaly/` exist to prevent.
    """
    ca, cb = _components(a), _components(b)
    drop: set[str] = set()
    if not (a.length_reliable and b.length_reliable):
        drop.add("log_length")
    if not (a.deck_readable and b.deck_readable):
        drop |= {"clutter", "masts"}

    total_w = 0.0
    acc = 0.0
    for name, (w, span) in FEATURE_WEIGHTS.items():
        if name in drop:
            continue
        total_w += w
        acc += w * ((ca[name] - cb[name]) / span) ** 2
    if total_w <= 0.0:
        return 1.0
    return math.sqrt(acc / total_w)


def observe(truth: Appearance, *, aspect_deg: Optional[float], quality: float,
            band: str, rng) -> Appearance:
    """What the camera actually measured, given the conditions of this look.

    Three degradations, each with a physical reason:

    * **Foreshortening.** The image carries ``length x |sin(aspect)|``. The
      station knows the aspect, so the measurement is divided back out — but the
      noise is divided out with it, so past :data:`MIN_ASPECT_FOR_LENGTH` the
      length is marked unreliable instead of being reported.
    * **Noise proportional to (1 - quality).** A poor image does not give wrong
      answers systematically; it gives noisy ones, and a classifier that does
      not widen with the noise will be confidently wrong at long range.
    * **Band.** A thermal image is a silhouette: deck clutter and mast count are
      simply not in it, and the descriptor says so rather than guessing.
    """
    from .conditions import BAND_THERMAL

    sigma = max(0.0, 1.0 - float(quality))
    sin_aspect = (1.0 if aspect_deg is None
                  else abs(math.sin(math.radians(aspect_deg))))
    reliable = sin_aspect >= MIN_ASPECT_FOR_LENGTH

    # Measure the apparent length, then correct for aspect. Both steps carry
    # their own error, which is why a bow-on look degrades so fast.
    apparent = truth.length_m * max(sin_aspect, 0.12)
    apparent *= 1.0 + rng.gauss(0.0, 0.10 * sigma + 0.03)
    measured_length = apparent / max(sin_aspect, MIN_ASPECT_FOR_LENGTH)

    deck_readable = band != BAND_THERMAL
    return Appearance(
        length_m=max(measured_length, 1.0),
        length_beam_ratio=max(
            1.5, truth.length_beam_ratio * (1.0 + rng.gauss(0.0, 0.12 * sigma
                                                            + 0.03))),
        superstructure_position=min(1.0, max(0.0,
            truth.superstructure_position + rng.gauss(0.0, 0.12 * sigma + 0.02))),
        freeboard_ratio=max(
            0.01, truth.freeboard_ratio * (1.0 + rng.gauss(0.0, 0.20 * sigma
                                                           + 0.05))),
        deck_clutter=min(1.0, max(0.0,
            truth.deck_clutter + rng.gauss(0.0, 0.18 * sigma + 0.04))),
        mast_count=max(0.0, truth.mast_count + rng.gauss(0.0, 0.5 * sigma)),
        length_reliable=reliable,
        deck_readable=deck_readable)
