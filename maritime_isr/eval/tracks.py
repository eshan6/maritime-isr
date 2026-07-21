"""Phase 2 evaluation — same discipline as Phase 1: metrics against truth,
appended to the eval ledger, regression-gated. Three acceptance numbers:

  fragmentation rate   fraction of true vessel-presence segments split into
                       more than one produced track (exit: <10%)
  gap classification   every truth dark/coverage interval matched to a
                       produced gap row; label accuracy reported
  encounter detector   precision/recall vs injected rendezvous + engineered
                       negatives (exit: precision >70%)
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from .harness import EvalResult, record


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


@dataclass
class TrackEvalResult:
    fragmentation_rate: float
    n_segments: int
    n_fragmented: int
    gap_confusion: dict            # {(expected, got): n}
    gap_label_accuracy: float
    encounter_precision: float
    encounter_recall: float
    encounter_f1: float
    n_enc_truth: int
    n_enc_pred: int


def evaluate_tracks(tracks: list, gaps: list[dict], encounters: list[dict],
                    truth: dict) -> TrackEvalResult:
    # ---- fragmentation: tracks whose span covers each truth segment ----
    n_frag, n_seg = 0, 0
    spoof_mmsis = {s["mmsi"] for s in truth.get("spoof", [])}
    for seg in truth["vessel_segments"]:
        if seg["mmsi"] in spoof_mmsis:
            continue  # spoof pairs legitimately produce 2 tracks — scored separately
        n_seg += 1
        t0, t1 = pd.Timestamp(seg["t0"]), pd.Timestamp(seg["t1"])
        covering = [tr for tr in tracks if tr.mmsi == seg["mmsi"]
                    and _overlap(tr.points["ts"].min(), tr.points["ts"].max(),
                                 t0, t1) > 0]
        if len(covering) > 1:
            n_frag += 1
    frag_rate = n_frag / n_seg if n_seg else 0.0

    # ---- gap classification vs truth dark periods ----
    confusion: dict[tuple[str, str], int] = {}
    correct = total = 0
    for dp in truth["dark_periods"]:
        t0, t1 = pd.Timestamp(dp["t0"]), pd.Timestamp(dp["t1"])
        dur = (t1 - t0).total_seconds()
        best, best_ov = None, 0.0
        for g in gaps:
            if g["mmsi"] != dp["mmsi"]:
                continue
            ov = _overlap(g["t_start"], g["t_end"], t0, t1)
            if ov > best_ov:
                best, best_ov = g, ov
        got = best["gap_type"] if best and best_ov > 0.5 * dur else "MISSED"
        confusion[(dp["expected"], got)] = confusion.get((dp["expected"], got), 0) + 1
        total += 1
        correct += int(got == dp["expected"])

    # ---- encounter precision/recall ----
    tp = 0
    used = set()
    for te in truth["encounters"]:
        t0, t1 = pd.Timestamp(te["t0"]), pd.Timestamp(te["t1"])
        pair = {te["mmsi_a"], te["mmsi_b"]}
        for k, e in enumerate(encounters):
            if k in used:
                continue
            if {e["mmsi_a"], e["mmsi_b"]} == pair and \
                    _overlap(e["t_start"], e["t_end"], t0, t1) > 0:
                tp += 1
                used.add(k)
                break
    fp = len(encounters) - len(used)
    fn = len(truth["encounters"]) - tp
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    return TrackEvalResult(
        fragmentation_rate=frag_rate, n_segments=n_seg, n_fragmented=n_frag,
        gap_confusion={f"{k[0]}->{k[1]}": v for k, v in confusion.items()},
        gap_label_accuracy=correct / total if total else 0.0,
        encounter_precision=prec, encounter_recall=rec, encounter_f1=f1,
        n_enc_truth=len(truth["encounters"]), n_enc_pred=len(encounters))


def record_to_ledger(r: TrackEvalResult, suite: str = "phase2_tracks_synthetic",
                     db_path=None) -> None:
    """Reuses the Phase 1 ledger table: P/R/F1 columns carry the encounter
    detector (the precision-gated number); everything else in detail_json."""
    res = EvalResult(
        suite=suite, n_scenes=0, n_truth=r.n_enc_truth, n_pred=r.n_enc_pred,
        tp=int(round(r.encounter_precision * r.n_enc_pred)),
        fp=r.n_enc_pred - int(round(r.encounter_precision * r.n_enc_pred)),
        fn=r.n_enc_truth - int(round(r.encounter_recall * r.n_enc_truth)),
        precision=r.encounter_precision, recall=r.encounter_recall,
        f1=r.encounter_f1, length_mae_m=float("nan"),
        fp_per_1000km2=float("nan"))
    record(res, detail=dict(
        fragmentation_rate=r.fragmentation_rate,
        n_segments=r.n_segments, n_fragmented=r.n_fragmented,
        gap_confusion=r.gap_confusion,
        gap_label_accuracy=r.gap_label_accuracy), db_path=db_path)
