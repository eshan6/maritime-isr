"""The classifier, behind an interface — and the library it classifies against.

*"Classification against a library, built behind a clean interface so that the
model can be swapped or supplied by the customer."* — Area 5.

**This is the commodity part and it is built to be thrown away.** Image
classification is the one piece of Area 5 that any competitor can also do, and
the brief says so outright. Everything of value in this area — deciding which
track to point a camera at, binding the image to the track, alerting on the
disagreement — lives on the other side of :class:`ImageClassifier`. Two
implementations ship here so that swapping is *demonstrated* rather than
asserted: :class:`PrototypeClassifier`, which uses everything a daylight image
carries, and :class:`SilhouetteClassifier`, which uses only what an outline
gives. `tests/test_area5.py` runs the same captures through both and through a
third defined in the test file, and nothing else in the loop changes.

Two design decisions carry the precision
----------------------------------------
**(a) The output vocabulary is measured, not declared** — the same move
ADR-033 made for vessel type from motion, for the same reason. A hand-written
list of coarse classes is a claim about the world; what this module is entitled
to make is a claim about *this model under these conditions*. So
:func:`measure_separability` classifies noisy samples of every prototype at a
stated image quality, reads the confusion, and merges any pair the model cannot
tell apart. It reuses :func:`tracks.vessel_type.confusable_groups` outright,
because it is the identical question one sensor along.

**(b) The vocabulary depends on the conditions of the look, and that is the
interesting part.** In a good daylight image the deck is readable, so a flush
tanker deck separates from a bulker's hatch coamings — a distinction motion can
*never* make (ADR-033: "a laden bulker and a laden product tanker at 13 knots on
a great-circle course are doing the same thing"). At night the image is a
thermal silhouette, the deck is gone, and the honest vocabulary collapses to
"large merchant". A model that reported "tanker" off a night silhouette would be
inventing the very feature it could not see.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

from ..config import PIPELINE_VERSION
from .appearance import Appearance, descriptor_for, distance, observe
from .conditions import BAND_THERMAL, BAND_VISIBLE

__all__ = ["ImageVerdict", "ImageClassifier", "PrototypeClassifier",
           "SilhouetteClassifier", "ReferenceLibrary", "PROTOTYPE_HULLS",
           "IMAGERY_TYPES", "imagery_group", "coarse_at",
           "measure_separability", "MIN_CLASSIFY_QUALITY",
           "MIN_IMAGE_CONFIDENCE", "IDENTITY_RADIUS", "IDENTITY_MARGIN"]


#: Below this image quality no claim is made at all.
#:
#: **Refusing is a first-class output**, exactly as it is in
#: `tracks.vessel_type.MIN_CONFIDENCE`. A camera slewed onto a hull at 19 km in
#: monsoon haze returns something; it does not return grounds for accusing her
#: of misdeclaring her type. This floor is what makes the "poor image" decoy
#: quiet rather than a false positive.
MIN_CLASSIFY_QUALITY = 0.35

#: Below this the classifier says `unclassified` rather than naming a type.
MIN_IMAGE_CONFIDENCE = 0.50

#: How sharply distance turns into probability. Small values make the nearest
#: prototype dominate; large ones flatten everything toward a tie. 0.08 puts a
#: clear separation at ~0.9 and a genuine ambiguity near 0.5, which is where
#: `MIN_IMAGE_CONFIDENCE` then does its work.
SOFTMAX_TEMPERATURE = 0.08

#: Re-recognition: how close two looks at one hull must be, and how much better
#: than the runner-up. **The margin matters more than the radius.** Two captures
#: of one ship in different light are not identical, so the radius has to be
#: loose; what stops that looseness becoming a wrong identification is requiring
#: the best match to beat the second by a clear gap. Without it, a library full
#: of similar merchants would return whichever one sorted first — the same
#: failure `ingest/pans/resolve.py` refuses when it declines to fuzzy-match a
#: transposed ship name.
IDENTITY_RADIUS = 0.085
IDENTITY_MARGIN = 0.045

#: Image quality at which the published vocabulary is measured: a good daylight
#: look at moderate range, which is the condition the cueing scheduler exists to
#: arrange. Stated rather than implicit, because the vocabulary is only
#: meaningful alongside the conditions it was measured under.
VOCABULARY_REFERENCE_QUALITY = 0.60

#: Typical dimensions per physical class, for the prototype descriptors.
#: Naval-architecture typicals — they are what the *classifier* compares
#: against, and they are deliberately independent of whatever dimensions the
#: corpus happens to mint, so a corpus cannot be built from the classifier's own
#: constants and then quoted as evidence about it.
PROTOTYPE_HULLS: dict[str, tuple[float, float, float]] = {
    # class: (length_m, beam_m, draught_m)
    "VLCC":           (330.0, 60.0, 20.0),
    "Suezmax":        (275.0, 48.0, 16.0),
    "Aframax":        (245.0, 42.0, 14.5),
    "product_tanker": (180.0, 32.0, 11.0),
    "bulker":         (190.0, 32.0, 11.5),
    "general_cargo":  (140.0, 21.0, 8.0),
    "container":      (200.0, 32.0, 11.0),
    "reefer":         (145.0, 22.0, 8.5),
    "fishing":         (26.0,  7.0,  3.0),
    "dhow":            (22.0,  6.0,  2.4),
    "naval":          (110.0, 14.0,  5.0),
}


def _prototypes() -> dict[str, Appearance]:
    return {k: descriptor_for(k, length_m=length, beam_m=beam, draught_m=dr)
            for k, (length, beam, dr) in PROTOTYPE_HULLS.items()}


PROTOTYPES: dict[str, Appearance] = _prototypes()


def _coarse_name(group: set[str]) -> str:
    """A readable name for a merged group.

    Deliberately generic, for the reason `tracks.vessel_type._coarse_name`
    gives: naming a merged tanker/bulker group "tanker" because tankers are the
    biggest member puts back exactly the false precision the merge removed.
    """
    tanker = {"VLCC", "Suezmax", "Aframax", "product_tanker"}
    dry = {"bulker", "general_cargo", "container", "reefer"}
    small = {"fishing", "dhow"}
    if group <= tanker:
        return "tanker"
    if group <= dry:
        return "dry_cargo"
    if group <= small:
        return "small_craft"
    if group <= (tanker | dry):
        return "merchant"
    if group <= (tanker | dry | small | {"naval"}):
        return "vessel"
    return "+".join(sorted(group))


def measure_separability(*, quality: float = VOCABULARY_REFERENCE_QUALITY,
                         band: str = BAND_VISIBLE, samples: int = 80,
                         seed: int = 11, aspect_deg: float = 75.0) -> dict:
    """Which prototypes this model can tell apart under these conditions.

    Generates ``samples`` noisy observations of every prototype at the stated
    quality and band, classifies each against the fine prototypes by nearest
    neighbour, and reads the confusion. Merging is delegated to
    :func:`tracks.vessel_type.confusable_groups` — the same union-find over the
    same 25% threshold — because the question is identical and two
    implementations of it would drift.

    Returns ``{"confusion", "groups", "vocabulary", "coarse_of", "conditions"}``.
    """
    from ..tracks.vessel_type import confusable_groups, confusion_matrix

    rng = random.Random(seed)
    y_true: list[str] = []
    y_pred: list[str] = []
    for name, proto in PROTOTYPES.items():
        for _ in range(samples):
            seen = observe(proto, aspect_deg=aspect_deg, quality=quality,
                           band=band, rng=rng)
            best = min(PROTOTYPES, key=lambda k: distance(seen, PROTOTYPES[k]))
            y_true.append(name)
            y_pred.append(best)

    cm = confusion_matrix(y_true, y_pred)
    groups = confusable_groups(cm)
    coarse_of: dict[str, str] = {}
    for g in groups:
        label = _coarse_name(set(g))
        for member in g:
            coarse_of[member] = label
    for name in PROTOTYPES:
        coarse_of.setdefault(name, name)
    vocabulary = sorted(set(coarse_of.values()))
    return {"confusion": cm, "groups": [sorted(g) for g in groups],
            "vocabulary": vocabulary, "coarse_of": coarse_of,
            "conditions": {"quality": quality, "band": band,
                           "aspect_deg": aspect_deg, "samples": samples}}


#: Cache keyed on (quality bucket, band). The measurement is deterministic, so
#: caching it changes nothing but the cost; bucketing to a tenth bounds the
#: cache at twenty entries and keeps the vocabulary from wobbling between two
#: captures whose quality differed in the third decimal place.
_SEPARABILITY_CACHE: dict[tuple[int, str], dict] = {}


def separability_at(quality: float, band: str) -> dict:
    key = (max(0, min(10, int(round(float(quality) * 10)))), band)
    hit = _SEPARABILITY_CACHE.get(key)
    if hit is None:
        hit = measure_separability(quality=key[0] / 10.0, band=band)
        _SEPARABILITY_CACHE[key] = hit
    return hit


def coarse_at(vessel_class: Optional[str], *, quality: float,
              band: str) -> Optional[str]:
    """The coarse label a class collapses to under these viewing conditions.

    Returns None for a class this model has no prototype for — which is a
    genuine "cannot compare", not a mismatch, and the rule that consumes it
    treats it that way.
    """
    if not vessel_class:
        return None
    return separability_at(quality, band)["coarse_of"].get(str(vessel_class))


#: The published vocabulary, at the reference conditions. For documentation and
#: for the surface; a live classification always uses its own capture's
#: conditions rather than this.
IMAGERY_TYPES: tuple[str, ...] = tuple(
    measure_separability()["vocabulary"])


def imagery_group(vessel_class: Optional[str]) -> Optional[str]:
    """The coarse imagery label for a class at the reference conditions."""
    return coarse_at(vessel_class, quality=VOCABULARY_REFERENCE_QUALITY,
                     band=BAND_VISIBLE)


# --------------------------------------------------------------------------
# the library
# --------------------------------------------------------------------------

@dataclass
class LibraryEntry:
    """One previously-imaged hull, as the system labelled her at the time."""
    subject_id: str
    appearance: Appearance
    capture_id: str
    at: float
    quality: float
    label: str = ""


class ReferenceLibrary:
    """Reference imagery, for type prototypes and for specific identity.

    Two populations and they are not the same thing:

    * the **class prototypes**, which are static and let a first-ever image of a
      hull be given a type;
    * the **hull entries**, which accumulate from captures this system has
      already taken, and let a later image be recognised as *the same ship*.

    The second is what the requirement means by "classify ... to specific
    identity where a vessel has been imaged before", and it is deliberately
    built out of the system's own captures rather than out of a supplied
    catalogue: a match therefore says "this looks like the hull we imaged at
    03:12 on the 14th", which may itself have been an unidentified contact. That
    is an honest claim and it is often the useful one — naming a dark contact as
    a hull seen before is exactly what a watchkeeper wants and is not the same
    as naming her from a registry.
    """

    def __init__(self, entries: Optional[Sequence[LibraryEntry]] = None,
                 *, max_per_subject: int = 4):
        self.entries: list[LibraryEntry] = list(entries or [])
        self.max_per_subject = max_per_subject

    def __len__(self) -> int:
        return len(self.entries)

    def subjects(self) -> set[str]:
        return {e.subject_id for e in self.entries}

    def add(self, entry: LibraryEntry) -> None:
        """Keep the best few looks per hull, not every look.

        A library that keeps everything is dominated by whichever ship happened
        to be imaged most, and nearest-neighbour then returns her for anything
        vaguely similar. Keeping the best few by image quality bounds that and
        keeps the entries that are actually worth matching against.
        """
        self.entries.append(entry)
        mine = [e for e in self.entries if e.subject_id == entry.subject_id]
        if len(mine) > self.max_per_subject:
            worst = min(mine, key=lambda e: e.quality)
            self.entries.remove(worst)

    def nearest(self, seen: Appearance, *, exclude_subject: Optional[str] = None
                ) -> tuple[Optional[LibraryEntry], float, float]:
        """Best entry, its distance, and the runner-up's distance.

        The runner-up comes back because the margin between them is what decides
        whether an identification is safe — see :data:`IDENTITY_MARGIN`.
        """
        best: Optional[LibraryEntry] = None
        d_best = math.inf
        d_second = math.inf
        for e in self.entries:
            if exclude_subject is not None and e.subject_id == exclude_subject:
                continue
            d = distance(seen, e.appearance)
            if d < d_best:
                if best is not None and best.subject_id != e.subject_id:
                    d_second = d_best
                d_best, best = d, e
            elif d < d_second and (best is None
                                   or e.subject_id != best.subject_id):
                d_second = d
        return best, d_best, d_second


# --------------------------------------------------------------------------
# the verdict and the interface
# --------------------------------------------------------------------------

@dataclass
class ImageVerdict:
    """One classification of one image, and what it rests on."""
    imaged_type: Optional[str] = None
    confidence: float = 0.0
    fine_type: Optional[str] = None
    probabilities: dict = field(default_factory=dict)
    identity_subject: Optional[str] = None
    identity_confidence: float = 0.0
    identity_basis: str = ""
    reason: str = ""
    #: Non-empty when no type claim was made, saying why. "We could not tell"
    #: and "she is a trawler" are different answers and the surface has to be
    #: able to render both (the same three-valued discipline as `anomaly/`).
    not_classifiable: str = ""
    model_name: str = ""
    model_provenance: str = ""
    #: The conditions the vocabulary was resolved under, carried so the rule
    #: downstream compares a declared class in the same coarse space.
    quality: float = 0.0
    band: str = BAND_VISIBLE
    pipeline_version: str = PIPELINE_VERSION

    @property
    def is_claim(self) -> bool:
        return bool(self.imaged_type) and not self.not_classifiable

    def as_dict(self) -> dict:
        return {"imaged_type": self.imaged_type,
                "type_confidence": round(float(self.confidence), 3),
                "fine_type": self.fine_type,
                "probabilities": {k: round(v, 3) for k, v in sorted(
                    self.probabilities.items(), key=lambda kv: -kv[1])},
                "identity_subject": self.identity_subject,
                "identity_confidence": round(float(self.identity_confidence), 3),
                "identity_basis": self.identity_basis,
                "reason": self.reason,
                "not_classifiable": self.not_classifiable,
                "model_name": self.model_name,
                "model_provenance": self.model_provenance,
                "quality": round(float(self.quality), 3),
                "band": self.band,
                "pipeline_version": self.pipeline_version}


class ImageClassifier(Protocol):
    """What a classifier must provide. Nothing else in Area 5 knows more.

    ``provenance`` is not decoration. Two of the six areas in this brief depend
    on components best borrowed rather than built, and the brief's standing
    caution is that both must be "documented as third-party, and swappable for
    whatever the customer already owns". A model that cannot say where it came
    from cannot be part of an evidence chain, so the field is required by the
    interface and it is written onto every capture row.
    """

    name: str
    provenance: str

    def classify(self, seen: Appearance, *, quality: float, band: str,
                 library: ReferenceLibrary,
                 known_subject: Optional[str] = None) -> ImageVerdict:
        ...


def _softmax(dists: dict[str, float], temperature: float) -> dict[str, float]:
    if not dists:
        return {}
    lo = min(dists.values())
    raw = {k: math.exp(-(d - lo) / max(temperature, 1e-6))
           for k, d in dists.items()}
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


class _NearestPrototype:
    """Shared machinery: distances to prototypes, coarse merge, identity."""

    name = "nearest-prototype"
    provenance = ("Built in-house as a deterministic stand-in. Not a vision "
                  "model: it consumes the six-number descriptor a vision "
                  "model would produce (see eo/appearance.py) and has never "
                  "seen an image. Replace it with the customer's own model "
                  "behind ImageClassifier.")
    #: Features this implementation is allowed to use. The subclass narrows it.
    uses: tuple[str, ...] = ()

    def _restrict(self, a: Appearance) -> Appearance:
        return a

    def classify(self, seen: Appearance, *, quality: float, band: str,
                 library: ReferenceLibrary,
                 known_subject: Optional[str] = None) -> ImageVerdict:
        v = ImageVerdict(model_name=self.name, model_provenance=self.provenance,
                         quality=float(quality), band=band)
        if quality < MIN_CLASSIFY_QUALITY:
            v.not_classifiable = (
                f"image quality {quality:.2f} is below the {MIN_CLASSIFY_QUALITY:.2f} "
                f"floor — there is not enough picture to classify")
            return v

        restricted = self._restrict(seen)
        dists = {k: distance(restricted, self._restrict(p))
                 for k, p in PROTOTYPES.items()}
        fine_probs = _softmax(dists, SOFTMAX_TEMPERATURE)
        coarse_of = separability_at(quality, band)["coarse_of"]

        coarse: dict[str, float] = {}
        for fine, p in fine_probs.items():
            label = coarse_of.get(fine, fine)
            coarse[label] = coarse.get(label, 0.0) + p
        top = max(coarse, key=lambda k: coarse[k])
        fine_top = min(dists, key=lambda k: dists[k])

        # Confidence is the coarse probability, held down by the quality of the
        # image it came from. A perfectly separated prototype match off a
        # marginal picture is still a marginal claim.
        conf = coarse[top] * (0.55 + 0.45 * float(quality))
        v.probabilities = coarse
        v.fine_type = fine_top
        if conf < MIN_IMAGE_CONFIDENCE:
            runner = sorted(coarse.items(), key=lambda kv: -kv[1])[1:2]
            v.not_classifiable = (
                f"best label {top!r} at {conf:.2f}, below the "
                f"{MIN_IMAGE_CONFIDENCE:.2f} bar"
                + (f"; {runner[0][0]!r} is nearly as likely" if runner else ""))
            v.confidence = conf
            return v

        v.imaged_type = top
        v.confidence = min(0.97, conf)
        bits = [f"nearest prototype {fine_top}",
                f"{'length withheld (bow-on)' if not seen.length_reliable else f'{seen.length_m:.0f} m'}",
                f"deck {'unreadable on thermal' if not seen.deck_readable else f'{seen.deck_clutter:.2f}'}"]
        v.reason = "; ".join(bits)

        # ---- identity, only where the library has something to match --------
        entry, d_best, d_second = library.nearest(restricted)
        if entry is not None and d_best <= IDENTITY_RADIUS:
            margin = d_second - d_best
            if margin >= IDENTITY_MARGIN or math.isinf(d_second):
                v.identity_subject = entry.subject_id
                v.identity_confidence = round(
                    min(0.9, (1.0 - d_best / IDENTITY_RADIUS) * 0.6 + 0.3), 3)
                v.identity_basis = (
                    f"matches the hull imaged in capture {entry.capture_id}"
                    f" at distance {d_best:.3f}, next best {d_second:.3f}")
            else:
                v.identity_basis = (
                    f"closest library hull is {d_best:.3f} away but the next is "
                    f"{d_second:.3f} — too close to call, so no identification "
                    f"is offered")
        return v


class PrototypeClassifier(_NearestPrototype):
    """The default. Uses every feature the image carries.

    Deterministic, dependency-free, and explicitly a stand-in. It exists so that
    the rest of Area 5 has something to consume, and so that swapping it can be
    demonstrated with a second implementation rather than promised.
    """

    name = "prototype-v1"


class SilhouetteClassifier(_NearestPrototype):
    """A second implementation, restricted to what an outline gives.

    Length and slenderness only — no deck, no superstructure position, no masts.
    It is what a thermal-only head, a very long-range look, or a coarse
    third-party model would be able to offer, and it is here to make the
    interface's swap-ability checkable: the same captures classified by both
    produce measurably different vocabularies and confidences, and not one line
    of the cueing, tagging or mismatch code changes between the two runs.
    """

    name = "silhouette-v1"
    provenance = ("Built in-house as a second stand-in, deliberately weaker "
                  "than the default, to demonstrate that the classifier is "
                  "replaceable. Not a vision model and has never seen an image.")

    def _restrict(self, a: Appearance) -> Appearance:
        # Deck clutter and mast count are dropped through the descriptor's own
        # "could not read" flag, which `appearance.distance` honours by removing
        # them from the comparison and renormalising — rather than by feeding
        # zeros, which would score an unreadable deck as a flush one.
        #
        # Superstructure position and freeboard are flattened to a constant.
        # `_restrict` is applied to the observation *and* to every prototype, so
        # a constant makes those two terms contribute exactly zero to every
        # distance. That is the honest way to say "this model does not use
        # them": no special case in the distance function, and no possibility of
        # the restriction being applied to one side only.
        return Appearance(
            length_m=a.length_m, length_beam_ratio=a.length_beam_ratio,
            superstructure_position=0.5, freeboard_ratio=0.09,
            deck_clutter=a.deck_clutter, mast_count=a.mast_count,
            length_reliable=a.length_reliable, deck_readable=False)
