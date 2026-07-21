"""Phase 3 evaluation. Three acceptance numbers, ledgered:

  association accuracy  correct MMSI on non-ambiguous matched contacts
                        whose truth is a transmitting vessel (target >=85%,
                        roadmap 3.4)
  dark precision        flagged dark candidates that are truly dark:
                        GHOSTS — vessels that never transmitted (target
                        >=70%, roadmap 3.5 / launch posture 3.3)
  dark recall           over ghost detections ABOVE the size floor (the
                        below-floor dhow is a stated capability boundary,
                        reported separately, not a miss)
  gap confirmation      a vessel detected mid-dark-period correctly matches
                        its own stale track — the right outcome is a match
                        flagged in_ais_gap (SAR-confirmed dark period), not
                        an unmatched contact. Scored as its own product.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from .harness import EvalResult, record


@dataclass
class FusionEvalResult:
    assoc_accuracy: float
    n_assoc_scored: int
    n_ambiguous: int
    dark_precision: float
    dark_recall: float
    dark_f1: float
    n_dark_flagged: int
    n_dark_truth: int
    suppression_counts: dict
    rig_suppressed_frac: float
    clutter_alert_count: int
    gap_confirm_rate: float          # dark-window vessel dets matched w/ flag
    n_gap_window_dets: int
    n_below_floor_ghosts: int


def _truly_dark(label: str, ts, dark_windows: list[dict]) -> bool:
    if label.startswith("ghost:"):
        return True
    if label.isdigit():
        t = pd.Timestamp(ts)
        for w in dark_windows:
            if int(label) == w["mmsi"] and \
                    pd.Timestamp(w["t0"]) <= t <= pd.Timestamp(w["t1"]):
                return True
    return False


def evaluate_fusion(associations: list[dict], verdicts: list[dict],
                    scene_truth: dict[str, str], feed_truth: dict,
                    ghost_lengths: dict[str, float] | None = None
                    ) -> FusionEvalResult:
    from ..config import DARK_MIN_LENGTH_M
    dark_windows = [w for w in feed_truth["dark_periods"]
                    if w["expected"] == "INTENTIONAL_SILENCE"]
    ghost_lengths = ghost_lengths or {}

    # ---- association accuracy on non-ambiguous vessel contacts ----------
    correct = scored = n_amb = 0
    gap_hits = gap_total = 0
    for a in associations:
        label = scene_truth.get(a["detection_id"], "")
        if a["status"] == "ambiguous":
            n_amb += 1
        if not label.isdigit():
            continue                      # rigs/clutter/ghosts scored via dark path
        if _truly_dark(label, a["ts"], dark_windows):
            # vessel detected mid-dark-window: correct outcome is a match
            # to its own track WITH the in_ais_gap flag
            gap_total += 1
            gap_hits += int(a["status"] in ("matched", "ambiguous")
                            and a.get("mmsi") == int(label)
                            and bool(a.get("in_ais_gap")))
            continue
        if a["status"] == "matched":
            scored += 1
            correct += int(a["mmsi"] == int(label))
        elif a["status"] == "unmatched":
            scored += 1                   # a transmitting vessel left unmatched
                                          # is an association miss — count it
    acc = correct / scored if scored else 0.0

    # ---- dark precision / recall (ghosts = never-transmitters) ----------
    flagged = [v for v in verdicts if v["status"] == "dark_candidate"]
    tp = sum(1 for v in flagged
             if scene_truth.get(v["detection_id"], "").startswith("ghost:"))
    ghost_dets = [d for d, lab in scene_truth.items() if lab.startswith("ghost:")]
    above_floor = [d for d in ghost_dets
                   if ghost_lengths.get(scene_truth[d],
                                        DARK_MIN_LENGTH_M + 1) >= DARK_MIN_LENGTH_M]
    prec = tp / len(flagged) if flagged else 0.0
    rec = tp / len(above_floor) if above_floor else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    supp: dict[str, int] = {}
    for v in verdicts:
        supp[v["status"]] = supp.get(v["status"], 0) + 1

    rig_ids = [d for d, lab in scene_truth.items() if lab.startswith("rig:")]
    rig_supp = sum(1 for v in verdicts if v["detection_id"] in rig_ids
                   and v["status"] == "suppressed_static")
    clutter_alerts = sum(1 for v in flagged
                         if scene_truth.get(v["detection_id"]) == "clutter")

    return FusionEvalResult(
        assoc_accuracy=acc, n_assoc_scored=scored, n_ambiguous=n_amb,
        dark_precision=prec, dark_recall=rec, dark_f1=f1,
        n_dark_flagged=len(flagged), n_dark_truth=len(above_floor),
        suppression_counts=supp,
        rig_suppressed_frac=rig_supp / len(rig_ids) if rig_ids else 1.0,
        clutter_alert_count=clutter_alerts,
        gap_confirm_rate=gap_hits / gap_total if gap_total else 0.0,
        n_gap_window_dets=gap_total,
        n_below_floor_ghosts=len(ghost_dets) - len(above_floor))


def _det_ts(det_id: str, verdicts: list[dict], associations: list[dict]):
    for v in verdicts:
        if v["detection_id"] == det_id:
            return v["ts"]
    for a in associations:
        if a["detection_id"] == det_id:
            return a["ts"]
    return pd.Timestamp(0, unit="s", tz="UTC")


def record_to_ledger(r: FusionEvalResult,
                     suite: str = "phase3_fusion_synthetic",
                     db_path=None) -> None:
    """P/R/F1 ledger columns carry the dark-vessel detector — the
    precision-gated product number. Association accuracy in detail_json."""
    res = EvalResult(
        suite=suite, n_scenes=0, n_truth=r.n_dark_truth,
        n_pred=r.n_dark_flagged,
        tp=int(round(r.dark_precision * r.n_dark_flagged)),
        fp=r.n_dark_flagged - int(round(r.dark_precision * r.n_dark_flagged)),
        fn=r.n_dark_truth - int(round(r.dark_recall * r.n_dark_truth)),
        precision=r.dark_precision, recall=r.dark_recall, f1=r.dark_f1,
        length_mae_m=float("nan"), fp_per_1000km2=float("nan"))
    record(res, detail=dict(
        assoc_accuracy=r.assoc_accuracy, n_assoc_scored=r.n_assoc_scored,
        n_ambiguous=r.n_ambiguous, suppression=r.suppression_counts,
        rig_suppressed_frac=r.rig_suppressed_frac,
        clutter_alert_count=r.clutter_alert_count,
        gap_confirm_rate=r.gap_confirm_rate,
        n_below_floor_ghosts=r.n_below_floor_ghosts), db_path=db_path)
