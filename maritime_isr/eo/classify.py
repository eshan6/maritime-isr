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
from .conditions import BAND_VISIBLE

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

#: Candidate softmax temperatures, searched during calibration.
#:
#: **The temperature is fitted, not chosen, and the first version chose it.**
#: A hand-set 0.08 produced a model that picked the right fine class 96% of the
#: time and reported an average confidence of 0.35 — so 84% of perfectly good
#: images were refused as "below the bar" by a number that had nothing to do
#: with how often the model was right. A confidence that does not track accuracy
#: is not a confidence, it is a decoration, and this whole project rests on an
#: operator being able to calibrate their trust against it (CLAUDE.md §4.3).
#:
#: :func:`measure_separability` therefore fits the temperature so that the mean
#: reported confidence matches the measured hit rate **under this capture's own
#: conditions** — ordinary temperature scaling, the standard remedy. A poor
#: image then produces low confidence because the model is genuinely less
#: often right on poor images, not because a multiplier was applied to it.
_TEMPERATURE_GRID = (0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.26, 0.40, 0.60,
                     0.90, 1.40, 2.20)

#: Re-recognition: how close two looks at one hull must be, and how much better
#: than the runner-up.
#:
#: **Both numbers are measured, and the measurement says something the feature
#: has to admit.** Over the prototype fleet at a good daylight look, two
#: observations of the *same* hull sit 0.12 apart at the median and 0.18 at the
#: ninetieth percentile — while the *closest* pair of different hulls sits 0.11
#: apart. The distributions overlap, and no radius separates them, because two
#: Suezmaxes of the same dimensions genuinely do look the same in six numbers.
#:
#: So the radius is set at the same-hull ninetieth percentile and the work is
#: done by the margin: an identification is offered only when the best match
#: beats the second by a clear gap, which happens when the hull is *distinctive*
#: relative to what the library holds and not when she is one of a class. That
#: is the honest capability — "this looks like the hull imaged on the 14th" for
#: an unusual shape, and a refusal for a sister ship — and it is the same
#: refusal `ingest/pans/resolve.py` makes rather than fuzzy-matching a
#: transposed ship name onto a different vessel.
IDENTITY_RADIUS = 0.18
IDENTITY_MARGIN = 0.06

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


def _softmax(dists: dict[str, float], temperature: float) -> dict[str, float]:
    if not dists:
        return {}
    lo = min(dists.values())
    raw = {k: math.exp(-(d - lo) / max(temperature, 1e-6))
           for k, d in dists.items()}
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


#: The level at which a declared vessel type can be held to account.
#:
#: **Not a vocabulary this project invented.** ITU-R M.1371 allocates the AIS
#: ship-and-cargo-type field in decades — 30 fishing, 60-69 passenger, 70-79
#: cargo, 80-89 tanker, 35 military — so a hull broadcasting a cargo code while
#: photographing as a tanker is contradicting the standard's own grouping, not
#: an opinion of ours. Grouping any finer would fire on the ordinary
#: disagreement between two people classifying one hull (ADR-034's
#: `class_quibble`); grouping any coarser would make everything a merchant and
#: the rule could never fire.
#:
#: A class absent from this table has **no family**, which is a refusal rather
#: than a default. `dhow` is deliberately absent: a dhow carries cargo, fishes,
#: and ferries passengers, and forcing her into one of those would manufacture
#: contradictions out of a hull type the standard does not cleanly cover.
AIS_TYPE_FAMILY: dict[str, str] = {
    "VLCC": "tanker", "Suezmax": "tanker", "Aframax": "tanker",
    "product_tanker": "tanker",
    "bulker": "cargo", "general_cargo": "cargo", "container": "cargo",
    "reefer": "cargo",
    "fishing": "fishing",
    "naval": "military",
}

#: Coarse group labels the classifier's merge produces, and the family each
#: belongs to. Anything not here falls through to :data:`AIS_TYPE_FAMILY`,
#: because an un-merged label *is* a fine class name.
_GROUP_FAMILY: dict[str, str] = {
    "tanker": "tanker", "dry_cargo": "cargo", "small_craft": "fishing",
}


def family_of_declared(vessel_class: Optional[str]) -> Optional[str]:
    """The AIS ship-type family a declared class belongs to, or None."""
    if not vessel_class:
        return None
    return AIS_TYPE_FAMILY.get(str(vessel_class))


def families_of_imaged(label: Optional[str], *, quality: Optional[float] = None,
                       band: Optional[str] = None,
                       coarse_of: Optional[dict] = None) -> Optional[frozenset]:
    """Every family an imagery label leaves open, or None if it bounds nothing.

    **A set, not a single family, and the difference decides whether the
    headline scenario can fire at all.** Under most conditions this model cannot
    separate a tanker from a bulker, so it publishes the merged label
    ``merchant``. Asking that label which family it *means* gives no answer —
    but the label still rules out every family it does not contain, and a hull
    broadcasting that she is a fishing vessel while imaging as a merchant has
    plainly been contradicted. A first version of this returned None there and
    let the brief's own headline example through untouched.

    So the question asked is "which families does this label leave open", and a
    contradiction is a declared family that is **not** in the set. The members
    are read from the merge measured at this capture's own conditions, so the
    answer narrows in a good daylight look and widens at night, on its own.

    Returns None when the set cannot be bounded — a label one of whose members
    is a class the AIS standard does not cleanly cover, which rules nothing out.

    **``coarse_of`` must come from the model that produced the label, and a
    measured defect is why the parameter exists.** The first version resolved
    every label against the *default* model's vocabulary. Swap in
    :class:`SilhouetteClassifier`, whose vocabulary at a marginal look collapses
    to ``small_craft`` and ``vessel``, and the default model — which holds no
    such labels — fell through to the fixed group table, read ``small_craft`` as
    meaning fishing and nothing else, and accused **36% of an entirely honest
    fleet**. A label means what the model that emitted it meant by it, so the
    classifier now publishes its own family set on the verdict and this fallback
    path is reached only for a third-party model that declines to.
    """
    if not label:
        return None
    if coarse_of is None:
        if quality is None or band is None:
            return None
        coarse_of = separability_at(quality, band)["coarse_of"]
    members = [fine for fine, group in coarse_of.items() if group == label]
    if not members:
        # A label from a model whose vocabulary this one does not hold — a
        # customer's own classifier, say. Fall back to the fixed tables rather
        # than refusing: an un-merged fine name and the three standard group
        # names are still interpretable.
        fam = _GROUP_FAMILY.get(str(label)) or AIS_TYPE_FAMILY.get(str(label))
        return frozenset({fam}) if fam else None
    fams = set()
    for m in members:
        fam = AIS_TYPE_FAMILY.get(m)
        if fam is None:
            return None
        fams.add(fam)
    return frozenset(fams)


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
                         band: str = BAND_VISIBLE, samples: int = 240,
                         seed: int = 11, aspect_deg: float = 75.0,
                         restrict=None) -> dict:
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
    keep = restrict or (lambda a: a)
    refs = {k: keep(p) for k, p in PROTOTYPES.items()}
    y_true: list[str] = []
    y_pred: list[str] = []
    dist_rows: list[dict[str, float]] = []
    for name, proto in PROTOTYPES.items():
        for _ in range(samples):
            seen = keep(observe(proto, aspect_deg=aspect_deg, quality=quality,
                                band=band, rng=rng))
            dists = {k: distance(seen, p) for k, p in refs.items()}
            best = min(dists, key=lambda k: dists[k])
            y_true.append(name)
            y_pred.append(best)
            dist_rows.append(dists)

    cm = confusion_matrix(y_true, y_pred)
    groups = confusable_groups(cm)
    merged = _merge_across_families(cm, groups)
    coarse_of: dict[str, str] = {}
    for g in merged:
        label = _coarse_name(set(g))
        for member in g:
            coarse_of[member] = label
    for name in PROTOTYPES:
        coarse_of.setdefault(name, name)
    vocabulary = sorted(set(coarse_of.values()))

    temperature, calibration = _calibrate(dist_rows, y_true, coarse_of)
    return {"confusion": cm,
            "groups": [sorted(g) for g in merged],
            "type_groups": [sorted(g) for g in groups],
            "vocabulary": vocabulary, "coarse_of": coarse_of,
            "temperature": temperature, "calibration": calibration,
            "conditions": {"quality": quality, "band": band,
                           "aspect_deg": aspect_deg, "samples": samples}}


#: How often a class may be mistaken for one in a *different* AIS ship-type
#: family before this model loses the right to name that family at all.
#:
#: **This threshold is the one that keeps the mismatch rule honest, and the
#: build did not have it until the false positives were counted.** The fine
#: confusion merge inherited from `tracks.vessel_type` asks "can the model tell
#: A from B", at 25%. That is the right question for *describing* a contact and
#: the wrong one for *accusing* a named hull, because the two are not the same
#: bar and they do not even weight the same errors: calling a bulker a general
#: cargo ship is a harmless slip inside one family, while calling her a product
#: tanker is the difference between silence and an alert.
#:
#: Measured on the prototypes at a good daylight look: `product_tanker` was
#: called `bulker` on 15% of samples and `bulker` was called `product_tanker` on
#: 12%. Both sat comfortably under the 25% type-merge bar, so both stayed
#: separate labels — and on 1,500 honest hulls the rule then produced 22 false
#: accusations, against two authored lies in the corpus. A ten-to-one false
#: positive rate is the alert-fatigue failure ADR-004 exists to prevent.
#:
#: 5% is set from what an accusation is worth rather than from what made the
#: numbers look good: a claim used to contradict a vessel's own declared
#: identity may be wrong across families at most one time in twenty *before*
#: corroboration, and `detect_imagery_mismatch` then requires more than one look
#: agreeing. Raising it would let the tanker/bulker pair back in; lowering it
#: collapses the vocabulary to "merchant" and the rule can never fire.
FAMILY_SEPARATION_THRESHOLD = 0.05


def _merge_across_families(cm: dict[str, dict[str, int]],
                           groups: list[set[str]]) -> list[set[str]]:
    """Merge any class this model cannot reliably place in an AIS family.

    Takes the type-level groups and unions two classes further whenever one is
    mistaken for the other across a family boundary more than
    :data:`FAMILY_SEPARATION_THRESHOLD` of the time. A merged group that spans
    two families is named by :func:`_coarse_name` as `merchant` (or `vessel`),
    and `family_of_imaged` returns None for those — so a capture that lands on
    one supports no contradiction at all, which is the honest outcome.

    Derived from the measured matrix, exactly as the type merge is. If a future
    feature genuinely separates a tanker's deck from a bulker's, the group
    dissolves on its own and nothing here needs editing.
    """
    parent = {c: c for c in cm}
    for g in groups:
        members = sorted(g)
        for m in members[1:]:
            parent[m] = members[0]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a in sorted(cm):
        fam_a = AIS_TYPE_FAMILY.get(a)
        total = sum(cm[a].values()) or 1
        for b, n in cm[a].items():
            fam_b = AIS_TYPE_FAMILY.get(b)
            if a == b or b not in parent:
                continue
            # Only a *cross-family* error disqualifies a family claim. A class
            # with no family (a dhow) can neither be placed nor mis-placed, so
            # it is left alone rather than dragged into a merge.
            if fam_a is None or fam_b is None or fam_a == fam_b:
                continue
            if n / total > FAMILY_SEPARATION_THRESHOLD:
                union(a, b)

    out: dict[str, set[str]] = {}
    for c in cm:
        out.setdefault(find(c), set()).add(c)
    return [g for g in out.values() if len(g) > 1]


def _coarse_probs(dists: dict[str, float], coarse_of: dict[str, str],
                  temperature: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for fine, p in _softmax(dists, temperature).items():
        label = coarse_of.get(fine, fine)
        out[label] = out.get(label, 0.0) + p
    return out


def _calibrate(dist_rows: list[dict[str, float]], y_true: list[str],
               coarse_of: dict[str, str]) -> tuple[float, dict]:
    """Fit the softmax temperature so mean confidence equals measured accuracy.

    Textbook temperature scaling. The model's *decision* is unaffected — the
    ranking of prototypes by distance does not depend on the temperature — so
    this changes only what the model says about how sure it is, which is
    precisely the thing that was wrong.

    Returns the fitted temperature and the calibration report, so the number is
    inspectable rather than a constant somebody has to trust.
    """
    if not dist_rows:
        return 0.10, {"accuracy": None, "mean_confidence": None, "n": 0}
    truth_coarse = [coarse_of.get(y, y) for y in y_true]
    hits = 0
    for dists, true_label in zip(dist_rows, truth_coarse):
        best = min(dists, key=lambda k: dists[k])
        hits += int(coarse_of.get(best, best) == true_label)
    accuracy = hits / len(dist_rows)

    best_t, best_gap, best_mean = _TEMPERATURE_GRID[0], None, 0.0
    for t in _TEMPERATURE_GRID:
        total = 0.0
        for dists in dist_rows:
            probs = _coarse_probs(dists, coarse_of, t)
            total += max(probs.values())
        mean_conf = total / len(dist_rows)
        gap = abs(mean_conf - accuracy)
        if best_gap is None or gap < best_gap:
            best_t, best_gap, best_mean = t, gap, mean_conf
    return best_t, {"accuracy": round(accuracy, 4),
                    "mean_confidence": round(best_mean, 4),
                    "temperature": best_t, "n": len(dist_rows)}


#: Cache keyed on (quality bucket, band). The measurement is deterministic, so
#: caching it changes nothing but the cost; bucketing to a tenth bounds the
#: cache at twenty entries and keeps the vocabulary from wobbling between two
#: captures whose quality differed in the third decimal place.
_SEPARABILITY_CACHE: dict[tuple[str, int, str], dict] = {}


def separability_at(quality: float, band: str, *, model: str = "full",
                    restrict=None) -> dict:
    """The vocabulary and the calibrated temperature for a model and a look.

    Keyed on the model as well as the conditions, because a weaker model must
    report weaker confidence: :class:`SilhouetteClassifier` sees fewer features,
    is right less often, and its calibration has to be measured on the features
    it actually uses. Sharing one calibration across implementations would let a
    restricted model inherit a fuller one's certainty, which is exactly the
    overclaim the interface exists to make impossible.
    """
    key = (model, max(0, min(10, int(round(float(quality) * 10)))), band)
    hit = _SEPARABILITY_CACHE.get(key)
    if hit is None:
        hit = measure_separability(quality=key[1] / 10.0, band=band,
                                   restrict=restrict)
        _SEPARABILITY_CACHE[key] = hit
    return hit


def coarse_at(vessel_class: Optional[str], *, quality: float,
              band: str) -> Optional[str]:
    """The coarse label a class collapses to under these viewing conditions.

    Returns None for a class this model has no prototype for — which is a
    genuine "cannot compare", not a mismatch, and the rule that consumes it
    treats it that way.

    **Always the full model's vocabulary, even when another model produced the
    verdict**, and that is safe rather than sloppy. The only use of this
    function in `anomaly.imagery` is the short-circuit "the image agrees with
    what she declares"; the guard that decides a *contradiction* is the AIS
    ship-type family comparison, which needs no vocabulary. A label from a
    coarser model simply fails to match here and falls through to the family
    test, which reaches the right answer by the longer route.
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
    #: Which AIS ship-type families this label leaves open, **as the model that
    #: produced it understands its own vocabulary**. None means it bounds
    #: nothing. Published here rather than re-derived downstream because a label
    #: means what its author meant by it — see :func:`families_of_imaged`.
    imaged_families: Optional[frozenset] = None
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
                "imaged_families": (sorted(self.imaged_families)
                                    if self.imaged_families else None),
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

    def _separability(self, quality: float, band: str) -> dict:
        return separability_at(quality, band, model=self.name,
                               restrict=self._restrict)

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
        sep = self._separability(quality, band)
        coarse_of = sep["coarse_of"]
        coarse = _coarse_probs(dists, coarse_of, sep["temperature"])
        top = max(coarse, key=lambda k: coarse[k])
        fine_top = min(dists, key=lambda k: dists[k])

        # **The confidence is the calibrated coarse probability and nothing is
        # applied on top of it.** An earlier version multiplied by a factor in
        # the image quality, which double-counts: the calibration samples are
        # generated *at this quality*, so a poor image already produces a low
        # number because the model is genuinely less often right on poor
        # images. Scaling it again would break the property that makes the
        # number worth anything — that it tracks the hit rate.
        conf = coarse[top]
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
        # What this label means, resolved in *this* model's own vocabulary.
        v.imaged_families = families_of_imaged(top, coarse_of=coarse_of)
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
