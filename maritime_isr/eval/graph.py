"""Phase 4 evaluation — the graph's acceptance numbers, ledgered.

  alert precision/recall  fired (rule, subject-mmsi) pairs vs the expected
                          set from the org-world truth. The expired-decoy
                          sanction and the shell-cycle vessel are the
                          precision traps: any alert through them is a FP.
  entity coverage         every AIS-active track attached to a vessel
                          entity (acceptance #1)
  migration_pass          add-edge-type with zero recompute (acceptance #3)
  inject_fired            canonical chain on a synthetic inject
  identity events         registry-diff renames/reflags/MMSI-swaps caught
"""
from __future__ import annotations

from dataclasses import dataclass

from .harness import EvalResult, record


@dataclass
class GraphEvalResult:
    alert_precision: float
    alert_recall: float
    alert_f1: float
    n_expected: int
    n_fired: int
    n_identity_events: int
    entity_coverage: float
    migration_pass: bool
    inject_fired: bool
    cycle_survived: bool


def evaluate_graph(store, alerts: list[dict], truth: dict,
                   id_events: list[dict], *, entity_coverage: float,
                   migration_pass: bool, inject_fired: bool
                   ) -> GraphEvalResult:
    from ..graph.identity import current_mmsi

    def key(rule: str, subject: str, at: float):
        node = store.node(subject)
        m = (node["props"].get("mmsi") if node else None) or \
            current_mmsi(store, subject, at)
        return (rule, m)

    expected = {(e["rule"], e["subject_mmsi"]) for e in truth["expected_alerts"]}
    fired = {key(a["rule"], a["subject"], a["ts"]) for a in alerts
             if not a["subject"].startswith("vessel:imo:99999")}  # injects excluded
    tp = len(expected & fired)
    prec = tp / len(fired) if fired else 0.0
    rec = tp / len(expected) if expected else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    # cycle survival: the shell-loop vessel must not have alerted, and the
    # engine must have completed (we're here, so it terminated)
    cycle_ok = ("sanctioned_owner_rendezvous",
                419500004) not in fired and ("sanctioned_owner_rendezvous",
                                             419500005) not in fired

    return GraphEvalResult(
        alert_precision=prec, alert_recall=rec, alert_f1=f1,
        n_expected=len(expected), n_fired=len(fired),
        n_identity_events=len(id_events),
        entity_coverage=entity_coverage, migration_pass=migration_pass,
        inject_fired=inject_fired, cycle_survived=cycle_ok)


def record_to_ledger(r: GraphEvalResult,
                     suite: str = "phase4_graph_synthetic",
                     db_path=None) -> None:
    res = EvalResult(
        suite=suite, n_scenes=0, n_truth=r.n_expected, n_pred=r.n_fired,
        tp=int(round(r.alert_precision * r.n_fired)),
        fp=r.n_fired - int(round(r.alert_precision * r.n_fired)),
        fn=r.n_expected - int(round(r.alert_recall * r.n_expected)),
        precision=r.alert_precision, recall=r.alert_recall, f1=r.alert_f1,
        length_mae_m=float("nan"), fp_per_1000km2=float("nan"))
    record(res, detail=dict(
        n_identity_events=r.n_identity_events,
        entity_coverage=r.entity_coverage,
        migration_pass=r.migration_pass, inject_fired=r.inject_fired,
        cycle_survived=r.cycle_survived), db_path=db_path)
