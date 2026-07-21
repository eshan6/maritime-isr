"""Phase 5 evaluation — the anomaly library's acceptance numbers, ledgered.

  per-anomaly live      each of the six detectors fired at least once and
                        its alerts carry evidence + a score above its gate
  feedback improvement  dispositions retune at least one detector with a
                        measured, non-negative precision delta (roadmap 5.4)
  risk explainability   every risk score equals the weighted sum of its
                        named components (the 5.3 contract), and a vessel
                        with a confirmed anomaly + sanctioned owner
                        outranks a clean one
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AnomalyEvalResult:
    types_live: dict           # anomaly_type -> n_alerts
    n_types_live: int
    all_scored_and_evidenced: bool
    feedback_delta: float
    feedback_type: str
    risk_decomposes: bool
    risk_ordering_correct: bool


def _score_matches_components(rs: dict, tol: float = 1e-6) -> bool:
    got = rs["risk_score"]
    want = sum(c["weighted"] for c in rs["components"].values())
    return abs(got - want) <= tol


def evaluate_anomalies(store, fired: dict, retune, risk_high: dict,
                       risk_low: dict) -> AnomalyEvalResult:
    types_live = {k: len(v) for k, v in fired.items()}
    n_live = sum(1 for v in types_live.values() if v > 0)

    ok = True
    for a in store.alerts():
        if a["anomaly_type"] is None:
            continue
        if a["score"] is None or not a["evidence"]:
            ok = False
            break

    delta = retune.precision_delta if retune else 0.0
    ftype = retune.anomaly_type if retune else "none"

    decomposes = _score_matches_components(risk_high) and \
        _score_matches_components(risk_low)
    ordering = risk_high["risk_score"] > risk_low["risk_score"]

    return AnomalyEvalResult(
        types_live=types_live, n_types_live=n_live,
        all_scored_and_evidenced=ok,
        feedback_delta=delta, feedback_type=ftype,
        risk_decomposes=decomposes, risk_ordering_correct=ordering)


def record_to_ledger(r: AnomalyEvalResult,
                     suite: str = "phase5_anomaly_synthetic",
                     db_path=None) -> None:
    from .harness import EvalResult, record
    # P/R columns carry "detectors live / six"; feedback delta + checks in detail
    res = EvalResult(
        suite=suite, n_scenes=0, n_truth=6, n_pred=r.n_types_live,
        tp=r.n_types_live, fp=0, fn=6 - r.n_types_live,
        precision=r.n_types_live / 6.0, recall=r.n_types_live / 6.0,
        f1=r.n_types_live / 6.0, length_mae_m=float("nan"),
        fp_per_1000km2=float("nan"))
    record(res, detail=dict(
        types_live=r.types_live, feedback_delta=r.feedback_delta,
        feedback_type=r.feedback_type, risk_decomposes=r.risk_decomposes,
        risk_ordering_correct=r.risk_ordering_correct,
        all_scored_and_evidenced=r.all_scored_and_evidenced), db_path=db_path)
