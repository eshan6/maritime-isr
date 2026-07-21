"""The proprietary feedback loop (roadmap 5.1/5.4).

Analyst dispositions are labels. This module turns accumulated labels into
a threshold retune for a detector and MEASURES the delta — the roadmap's
"disposition feedback measurably improves at least one detector" gate.

The mechanism is deliberately simple and auditable, not a learned model:
given a detector's disposed alerts (confirm=positive, dismiss=negative),
find the score threshold that maximizes precision subject to keeping recall
above a floor, and report precision/recall before (current threshold) and
after (proposed threshold). A competitor cloning the architecture cannot
replicate this, because the labels are the asset — the model is trivial.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import ANOMALY_THRESHOLDS, FEEDBACK_MIN_DISPOSITIONS


@dataclass
class RetuneResult:
    anomaly_type: str
    n_dispositions: int
    old_threshold: float
    new_threshold: float
    precision_before: float
    recall_before: float
    precision_after: float
    recall_after: float
    applied: bool

    @property
    def precision_delta(self) -> float:
        return self.precision_after - self.precision_before


def _pr(labelled: list[tuple[float, bool]], thr: float) -> tuple[float, float]:
    """precision, recall at a threshold. labelled: [(score, is_true)]."""
    flagged = [t for s, t in labelled if s >= thr]
    tp = sum(flagged)
    all_true = sum(t for _, t in labelled)
    prec = tp / len(flagged) if flagged else 0.0
    rec = tp / all_true if all_true else 0.0
    return prec, rec


def propose_retune(store, anomaly_type: str, *, recall_floor: float = 0.5,
                   apply: bool = False) -> RetuneResult | None:
    """Retune one detector from its dispositions. Returns None if there
    isn't enough labelled data yet (guards against overfitting to 2 clicks)."""
    disps = store.dispositions(anomaly_type)
    labelled = [(d["score"], d["label"] == "confirm")
                for d in disps if d["label"] in ("confirm", "dismiss")
                and d["score"] is not None]
    if len(labelled) < FEEDBACK_MIN_DISPOSITIONS:
        return None

    old = ANOMALY_THRESHOLDS[anomaly_type]
    p0, r0 = _pr(labelled, old)

    # candidate thresholds: every observed score, pick best precision with
    # recall >= floor; ties break toward lower threshold (preserve recall)
    cands = sorted({s for s, _ in labelled})
    best = (old, p0, r0)
    for thr in cands:
        p, r = _pr(labelled, thr)
        if r >= recall_floor and (p > best[1] or
                                  (p == best[1] and thr < best[0])):
            best = (thr, p, r)
    new_thr, p1, r1 = best

    result = RetuneResult(
        anomaly_type=anomaly_type, n_dispositions=len(labelled),
        old_threshold=old, new_threshold=new_thr,
        precision_before=p0, recall_before=r0,
        precision_after=p1, recall_after=r1, applied=False)
    if apply and new_thr != old:
        ANOMALY_THRESHOLDS[anomaly_type] = new_thr   # runtime retune
        result.applied = True
    return result


def feedback_summary(store) -> dict:
    """Per-detector disposition tally — the loop's health at a glance."""
    out = {}
    for atype in ANOMALY_THRESHOLDS:
        disps = store.dispositions(atype)
        conf = sum(d["label"] == "confirm" for d in disps)
        dism = sum(d["label"] == "dismiss" for d in disps)
        watch = sum(d["label"] == "watch" for d in disps)
        n = len(disps)
        out[atype] = dict(
            n=n, confirm=conf, dismiss=dism, watch=watch,
            realized_precision=(conf / (conf + dism) if conf + dism else None),
            threshold=ANOMALY_THRESHOLDS[atype])
    return out
