"""What *kind* of ship is that, from motion alone? — Area 3.

*"Radar today gives kinematics and position and nothing else. It does not
classify vessel type, activities, or interactions between vessels. The ask is
all three, from radar."* — the IDEX Challenge 82 brief, Area 3.

*"A radar track has no identity. Everything must come from motion alone. This is
a genuine inference problem, and it is also the one the Coast Guard most needs
solved, because radar is their primary sensor and it is the only sensor that
sees a vessel which does not want to be seen."*

The one design decision that matters
------------------------------------
The brief is explicit about it, and it is the opposite of what a demo wants to
do: *"It will not distinguish everything, and it should not pretend to. A small
set of classes it can genuinely separate, with honest confidence, is worth far
more than a long list of classes it guesses at. Report the confusion matrix and
state plainly which classes it cannot tell apart."*

So this module does something slightly unusual. It trains on the **fine**
classes the registry declares, measures the confusion between them, and then
**derives the output vocabulary from that measurement** — classes the model
cannot separate are merged into one honest coarse label rather than reported as
a coin flip between two confident-looking names. :func:`confusable_groups` is
what does the merging, and it reads the confusion matrix rather than a hand-
written list, so the vocabulary tracks the model's actual ability.

Measured on this corpus, the medians say the answer before any model runs:

    class            n   sog_p90  sog_p50   turn   straightness   spread_km
    fishing         44       9.9      3.7   3.23           0.44       197.8
    reefer          10      18.3     17.9   0.78           0.74       720.8
    product_tanker  42      13.1     12.9   0.99           0.67       404.4
    general_cargo   40      12.7     10.5   1.34           0.68       407.4
    bulker          38      12.9      8.9   1.30           0.60       353.9
    Aframax         17      14.4      9.5   1.27           0.53       299.8

Fishing is unmistakable — a third of the speed and three times the turn rate.
A reefer runs fast enough to stand out. **The tanker/bulker/general-cargo
cluster is not separable from motion and never will be**, because a laden
bulker and a laden product tanker at 13 knots on a great-circle course are
doing the same thing. Saying so is the product; guessing between them is not.

Training discipline
-------------------
**Split by vessel, never by track.** CLAUDE.md's anti-pattern list bans
splitting by chip rather than by scene, because chips from one scene leak across
the split and inflate the metric. Tracks from one hull are the same hazard one
domain along: a vessel that appears in both halves lets the model memorise her
rather than her class. :func:`train` groups by hull and refuses a split that
would put one hull on both sides.

**Trained on AIS, applied to radar.** The labels come from vessels that
broadcast an identity; the inference runs on contacts that do not. That is the
transfer the requirement actually needs, and it is only sound because the
features are motion and nothing else — no MMSI, no message rate, nothing that
exists on one sensor and not the other.

Every number this module produces is measured on the synthetic corpus. Real
performance will be lower and must be re-measured on the deploy host before any
figure is stated externally (CLAUDE.md §4.6).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from ..config import PIPELINE_VERSION
from ..coastline import distance_to_shore_km
from ..ports import PORTS
from .activity import activity_features
from .kalman import epoch_s

__all__ = ["TypeVerdict", "VesselTypeModel", "type_features", "train",
           "confusion_matrix", "confusable_groups", "FEATURE_NAMES",
           "MIN_TRACK_POINTS", "MIN_CONFIDENCE"]


#: A track shorter than this cannot support a type claim. Sized from what the
#: features need rather than from taste: a speed distribution needs enough
#: samples to have percentiles, and a turn-rate mean over ten fixes is noise.
MIN_TRACK_POINTS = 30

#: Below this the model says `unclassified` rather than naming a class.
#:
#: **Refusing is a first-class output** — the brief's "it should not pretend to"
#: made operational. A calibrated 0.4 between two merchant classes means the
#: model does not know, and printing the higher of the two on an operator's
#: screen converts "I don't know" into a confident wrong answer.
MIN_CONFIDENCE = 0.45

#: How similar two classes must look before they are merged into one coarse
#: label. Read as: if more than this fraction of class A's tracks are called B,
#: the pair is not separable and reporting them apart is a fiction.
CONFUSION_MERGE_THRESHOLD = 0.25

FEATURE_NAMES = (
    "sog_median", "sog_p90", "sog_p10", "sog_std",
    "turn_rate_deg_min", "straightness", "spread_km",
    "slow_fraction", "fast_fraction", "stopped_fraction",
    "dist_to_nearest_port_km", "dist_to_shore_km",
    "span_hours", "night_fraction_moving",
)


def _hav_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def type_features(track) -> Optional[dict]:
    """The motion signature a type claim is made from, or None if too short.

    Everything here is derived from position, speed and course over time.
    **Nothing reads an identifier, a message rate or a sensor name**, which is
    what makes a model trained on AIS tracks applicable to radar contacts —
    the property Area 3 depends on and `test_type_features_are_sensor_blind`
    enforces.
    """
    base = activity_features(track)
    if base.get("n_points", 0) < MIN_TRACK_POINTS:
        return None

    pts = track.points[track.points.quality != "outlier"]
    sog = pts["sog_kn"].to_numpy(dtype=float)
    t = epoch_s(pts["ts"])

    # How much of her time is spent in each speed regime. A trawler alternates
    # between steaming and towing; a merchant is at service speed or stopped.
    n = len(sog)
    slow = float(np.count_nonzero((sog > 1.0) & (sog < 6.0)) / n)
    fast = float(np.count_nonzero(sog >= 10.0) / n)
    stopped = float(np.count_nonzero(sog <= 1.0) / n)

    # Where she works, relative to the land. Two different quantities and both
    # are signal: a working trawler stays inshore and ranges a ground, a
    # merchant runs between terminals and crosses open water.
    clat, clon = base["lat"], base["lon"]
    d_port = min((_hav_km(clat, clon, la, lo) for la, lo in PORTS.values()),
                 default=float("nan"))

    # **Distance from shore, which the brief names and this feature set was
    # faking.** It was `dist_to_nearest_port_km` alone, and that is a different
    # quantity: the gazetteer holds 34 ports along 2,000 km of coast, so a hull
    # working five miles off an empty beach scored as 120 km "from shore". The
    # real measure comes from the same 1 km land mask the SAR detector and the
    # corpus validator use, so the three cannot disagree about where the sea is.
    d_shore = float(distance_to_shore_km(clat, clon))

    # Diurnal rhythm — the brief names it. Fishing effort is concentrated
    # around dawn and dusk in many fisheries and merchant traffic is not, so
    # "what fraction of her *moving* time is at night" carries signal that
    # speed alone does not. UTC hour is a crude proxy for local solar time and
    # is honest at this longitude range (60-78E is under two hours wide).
    hours = ((t / 3600.0) % 24.0 + 5.5) % 24.0        # roughly IST
    moving = sog > 1.0
    night = (hours < 6.0) | (hours >= 18.0)
    n_moving = int(np.count_nonzero(moving))
    night_moving = (float(np.count_nonzero(moving & night)) / n_moving
                    if n_moving else 0.0)

    return {
        "sog_median": float(np.median(sog)),
        "sog_p90": float(np.percentile(sog, 90)),
        "sog_p10": float(np.percentile(sog, 10)),
        "sog_std": float(np.std(sog)),
        "turn_rate_deg_min": float(base["turn_rate_deg_min"]),
        "straightness": float(base["straightness"]),
        "spread_km": float(base["spread_m"]) / 1000.0,
        "slow_fraction": slow,
        "fast_fraction": fast,
        "stopped_fraction": stopped,
        "dist_to_nearest_port_km": float(d_port),
        "dist_to_shore_km": d_shore,
        "span_hours": float(base["span_minutes"]) / 60.0,
        "night_fraction_moving": float(night_moving),
    }


def _vector(feats: dict) -> list[float]:
    return [float(feats.get(k, 0.0)) for k in FEATURE_NAMES]


@dataclass
class TypeVerdict:
    """One type claim about one track, with what it rests on."""
    vessel_type: str
    confidence: float
    #: Probability over every coarse class, so an operator can see the runners-up
    #: rather than only the winner.
    probabilities: dict = field(default_factory=dict)
    #: The fine class the model actually predicted, before coarse merging —
    #: kept because "we think bulker, but bulker and tanker are indistinguishable
    #: to us" is more informative than either half alone.
    fine_type: Optional[str] = None
    reason: str = ""
    features: dict = field(default_factory=dict)
    track_id: Optional[str] = None
    track_source: Optional[str] = None
    pipeline_version: str = PIPELINE_VERSION

    @property
    def is_claim(self) -> bool:
        return self.vessel_type != "unclassified"

    def as_dict(self) -> dict:
        return {
            "vessel_type": self.vessel_type,
            "confidence": round(float(self.confidence), 3),
            "fine_type": self.fine_type,
            "probabilities": {k: round(v, 3)
                              for k, v in sorted(self.probabilities.items(),
                                                 key=lambda kv: -kv[1])},
            "reason": self.reason,
            "features": {k: round(v, 3) for k, v in self.features.items()},
            "track_id": self.track_id, "track_source": self.track_source,
            "pipeline_version": self.pipeline_version,
        }


def confusion_matrix(y_true: Sequence[str], y_pred: Sequence[str]
                     ) -> dict[str, dict[str, int]]:
    """{true_class: {predicted_class: n}}. Plain dict so it serialises."""
    out: dict[str, dict[str, int]] = defaultdict(Counter)
    for a, b in zip(y_true, y_pred):
        out[a][b] += 1
    return {k: dict(v) for k, v in out.items()}


def confusable_groups(cm: dict[str, dict[str, int]],
                      threshold: float = CONFUSION_MERGE_THRESHOLD
                      ) -> list[set[str]]:
    """Classes the model cannot tell apart, read off the confusion matrix.

    **The vocabulary is derived, not declared.** A hand-written "these are the
    coarse classes" list is a claim about the world; this is a claim about the
    model, which is the only one this module is entitled to make. If a later
    feature genuinely separates bulkers from tankers, the groups shrink on their
    own and nothing here needs editing.

    Two classes join a group when either one is mistaken for the other more than
    `threshold` of the time — asymmetric on purpose, because a model that calls
    every tanker a bulker but never the reverse still cannot be trusted to say
    "tanker".
    """
    labels = sorted(cm)
    parent = {c: c for c in labels}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a in labels:
        total = sum(cm[a].values()) or 1
        for b, n in cm[a].items():
            if a == b or b not in parent:
                continue
            if n / total > threshold:
                union(a, b)

    groups: dict[str, set[str]] = defaultdict(set)
    for c in labels:
        groups[find(c)].add(c)
    return [g for g in groups.values() if len(g) > 1]


def _coarse_name(group: set[str]) -> str:
    """A readable name for a merged group.

    Deliberately generic. Naming a merged tanker/bulker/cargo group "tanker"
    because tankers are the largest member would reintroduce exactly the false
    precision the merge exists to remove.
    """
    merchant = {"product_tanker", "bulker", "general_cargo", "Aframax",
                "Suezmax", "VLCC", "reefer", "container"}
    if group <= merchant:
        return "merchant"
    if group <= {"fishing", "dhow"}:
        return "small_craft"
    return "+".join(sorted(group))


@dataclass
class VesselTypeModel:
    """A fitted classifier plus the honesty it was measured with."""
    classes: list[str]
    coarse_of: dict[str, str]
    confusion: dict
    groups: list[list[str]]
    n_train: int
    n_test: int
    accuracy_fine: float
    accuracy_coarse: float
    _clf: object = None
    pipeline_version: str = PIPELINE_VERSION

    # -- inference ------------------------------------------------------
    def classify(self, track) -> TypeVerdict:
        f = type_features(track)
        tid = getattr(track, "track_id", None)
        src = getattr(getattr(track, "source", None), "name", None)
        if f is None:
            return TypeVerdict(
                "unclassified", 0.0, track_id=tid, track_source=src,
                reason=(f"Fewer than {MIN_TRACK_POINTS} usable fixes. A speed "
                        f"distribution needs samples to have percentiles, so "
                        f"nothing is claimed."))

        proba = self._clf.predict_proba([_vector(f)])[0]
        fine = {c: float(p) for c, p in zip(self._clf.classes_, proba)}

        # Collapse to the coarse vocabulary the confusion matrix supports.
        coarse: dict[str, float] = defaultdict(float)
        for c, p in fine.items():
            coarse[self.coarse_of.get(c, c)] += p
        best = max(coarse, key=coarse.get)
        conf = coarse[best]
        fine_best = max(fine, key=fine.get)

        if conf < MIN_CONFIDENCE:
            runner = sorted(coarse.items(), key=lambda kv: -kv[1])[:2]
            return TypeVerdict(
                "unclassified", conf, probabilities=dict(coarse),
                fine_type=fine_best, features=f, track_id=tid,
                track_source=src,
                reason=(f"Best class {best} at {conf:.2f}, under the "
                        f"{MIN_CONFIDENCE} bar"
                        + (f" against {runner[1][0]} at {runner[1][1]:.2f}"
                           if len(runner) > 1 else "")
                        + ". The motion does not distinguish them, so no type "
                          "is claimed."))

        merged = [g for g in self.groups if fine_best in g]
        note = ""
        if merged:
            others = sorted(set(merged[0]) - {fine_best})
            note = (f" Reported as '{best}' rather than '{fine_best}' because "
                    f"this model cannot separate it from "
                    f"{', '.join(others)} on motion alone.")
        return TypeVerdict(
            best, conf, probabilities=dict(coarse), fine_type=fine_best,
            features=f, track_id=tid, track_source=src,
            reason=(f"Motion signature: {f['sog_median']:.1f} kn median, "
                    f"{f['turn_rate_deg_min']:.1f}°/min turn rate, "
                    f"straightness {f['straightness']:.2f}, ranging "
                    f"{f['spread_km']:.0f} km.{note}"))

    def report(self) -> dict:
        """Everything a reader needs to judge the model, including its limits."""
        return {
            "classes": list(self.classes),
            "coarse_vocabulary": sorted(set(self.coarse_of.values())),
            "cannot_separate": [sorted(g) for g in self.groups],
            "confusion_matrix": self.confusion,
            "n_train_tracks": self.n_train,
            "n_test_tracks": self.n_test,
            "accuracy_fine": round(self.accuracy_fine, 4),
            "accuracy_coarse": round(self.accuracy_coarse, 4),
            "min_confidence": MIN_CONFIDENCE,
            "min_track_points": MIN_TRACK_POINTS,
            "features": list(FEATURE_NAMES),
            "caveat": (
                "Measured on the synthetic scenario corpus, trained on tracks "
                "whose class the generator also chose. Real performance will "
                "be lower and must be re-measured on the deploy host before "
                "any figure is stated externally (CLAUDE.md §4.6)."),
            "pipeline_version": self.pipeline_version,
        }


def train(labelled: Iterable[tuple[str, str, object]], *,
          test_fraction: float = 0.3, seed: int = 7) -> Optional[VesselTypeModel]:
    """Fit a type classifier. `labelled` is (hull_key, class, track).

    ``hull_key`` groups tracks belonging to one vessel and **the split is made
    on it, never on the track** — CLAUDE.md's chip-versus-scene rule, one domain
    along. A hull on both sides of the split lets the model memorise her rather
    than her class, and the accuracy it reports would be a measurement of
    nothing.

    Returns None when there is not enough labelled data to measure anything,
    rather than a model whose accuracy is an artefact of a six-track test set.
    """
    from sklearn.ensemble import RandomForestClassifier

    rows: list[tuple[str, str, list[float]]] = []
    for hull, cls, track in labelled:
        if not cls or cls == "unknown":
            continue
        f = type_features(track)
        if f is None:
            continue
        rows.append((str(hull), str(cls), _vector(f)))
    if len(rows) < 40:
        return None

    # Group-aware split: hulls, shuffled, allocated whole to one side.
    hulls = sorted({h for h, _, _ in rows})
    rng = np.random.default_rng(seed)
    rng.shuffle(hulls)
    n_test = max(1, int(len(hulls) * test_fraction))
    test_hulls = set(hulls[:n_test])

    Xtr = [v for h, _, v in rows if h not in test_hulls]
    ytr = [c for h, c, _ in rows if h not in test_hulls]
    Xte = [v for h, _, v in rows if h in test_hulls]
    yte = [c for h, c, _ in rows if h in test_hulls]
    if len(set(ytr)) < 2 or not Xte:
        return None

    clf = RandomForestClassifier(
        n_estimators=300, min_samples_leaf=2, random_state=seed,
        class_weight="balanced_subsample")
    clf.fit(Xtr, ytr)

    pred = list(clf.predict(Xte))
    cm = confusion_matrix(yte, pred)
    groups = confusable_groups(cm)

    coarse_of: dict[str, str] = {}
    for g in groups:
        name = _coarse_name(g)
        for c in g:
            coarse_of[c] = name
    for c in clf.classes_:
        coarse_of.setdefault(str(c), str(c))

    acc_fine = sum(1 for a, b in zip(yte, pred) if a == b) / len(yte)
    acc_coarse = sum(1 for a, b in zip(yte, pred)
                     if coarse_of.get(a, a) == coarse_of.get(b, b)) / len(yte)

    # Refit on everything once the honest measurement is taken. The held-out
    # split exists to *measure*; throwing away 30% of the labels at serving
    # time would be paying for the measurement twice.
    final = RandomForestClassifier(
        n_estimators=300, min_samples_leaf=2, random_state=seed,
        class_weight="balanced_subsample")
    final.fit([v for _, _, v in rows], [c for _, c, _ in rows])

    return VesselTypeModel(
        classes=sorted({c for _, c, _ in rows}), coarse_of=coarse_of,
        confusion=cm, groups=[sorted(g) for g in groups],
        n_train=len(Xtr), n_test=len(Xte),
        accuracy_fine=acc_fine, accuracy_coarse=acc_coarse, _clf=final)


def format_confusion(cm: dict[str, dict[str, int]]) -> str:
    """The confusion matrix as text, because the brief asks for it by name."""
    labels = sorted(set(cm) | {p for row in cm.values() for p in row})
    w = max((len(x) for x in labels), default=8) + 1
    head = " " * (w + 2) + "".join(f"{x[:9]:>10}" for x in labels)
    lines = [head]
    for a in labels:
        row = cm.get(a, {})
        lines.append(f"{a:<{w}} |" + "".join(f"{row.get(b, 0):>10}"
                                             for b in labels))
    return "\n".join(lines)
