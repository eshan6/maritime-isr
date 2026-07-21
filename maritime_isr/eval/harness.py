"""Evaluation harness (roadmap 1.3) — a permanent fixture, not a phase
deliverable. Every model change runs this; every run is persisted to the
catalog's eval ledger so a silent regression is a query away, not an
analyst-trust crater six weeks downstream.

Matching: global assignment (scipy linear_sum_assignment) between
predicted and truth positions inside a gate radius — *not* greedy nearest,
for the same reason Phase 3 bans greedy association: greedy double-counts
and manufactures phantom errors.

Metrics per run (roadmap 1.3, verbatim):
- precision / recall / F1 at matched-detection level
- length-estimation MAE on matched pairs
- **false positives per 1,000 km² of ocean** — the operationally
  meaningful number; an analyst feels FP density, not F1.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import CATALOG_DB, PIPELINE_VERSION
from ..provenance import now_iso

MATCH_GATE_M = 300.0  # a matched detection must fall within this of truth

_EVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY,
  suite TEXT NOT NULL,               -- synthetic_aoi | xview3_holdout | aoi_handlabeled
  pipeline_version TEXT NOT NULL,
  n_scenes INTEGER, n_truth INTEGER, n_pred INTEGER,
  tp INTEGER, fp INTEGER, fn INTEGER,
  precision REAL, recall REAL, f1 REAL,
  length_mae_m REAL,
  fp_per_1000km2 REAL,
  detail_json TEXT,
  ran_at TEXT NOT NULL
);
"""


@dataclass
class EvalResult:
    suite: str
    n_scenes: int
    n_truth: int
    n_pred: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    length_mae_m: float
    fp_per_1000km2: float


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.deg2rad((lat1 + lat2) / 2))
    return float(np.hypot((lat1 - lat2) * m_per_deg_lat,
                          (lon1 - lon2) * m_per_deg_lon))


def match_scene(pred: list[dict], truth: list[dict],
                gate_m: float = MATCH_GATE_M):
    """pred/truth: dicts with lat, lon, optional length_m.
    Returns (matched index pairs, fp indices, fn indices)."""
    if not pred or not truth:
        return [], list(range(len(pred))), list(range(len(truth)))
    D = np.full((len(pred), len(truth)), 1e12)
    for i, p in enumerate(pred):
        for j, t in enumerate(truth):
            d = _dist_m(p["lat"], p["lon"], t["lat"], t["lon"])
            if d <= gate_m:
                D[i, j] = d
    ri, ci = linear_sum_assignment(D)
    pairs = [(i, j) for i, j in zip(ri, ci) if D[i, j] <= gate_m]
    matched_p = {i for i, _ in pairs}
    matched_t = {j for _, j in pairs}
    fp = [i for i in range(len(pred)) if i not in matched_p]
    fn = [j for j in range(len(truth)) if j not in matched_t]
    return pairs, fp, fn


def evaluate(scenes: list[dict], suite: str) -> EvalResult:
    """scenes: [{pred: [...], truth: [...], ocean_area_km2: float}, ...]"""
    tp = fp = fn = 0
    length_errs: list[float] = []
    ocean_km2 = 0.0
    for s in scenes:
        pairs, fpi, fni = match_scene(s["pred"], s["truth"])
        tp += len(pairs); fp += len(fpi); fn += len(fni)
        ocean_km2 += float(s.get("ocean_area_km2", 0.0))
        for i, j in pairs:
            pl, tl = s["pred"][i].get("length_m"), s["truth"][j].get("length_m")
            if pl is not None and tl is not None:
                length_errs.append(abs(pl - tl))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return EvalResult(
        suite=suite, n_scenes=len(scenes),
        n_truth=tp + fn, n_pred=tp + fp, tp=tp, fp=fp, fn=fn,
        precision=precision, recall=recall, f1=f1,
        length_mae_m=float(np.mean(length_errs)) if length_errs else float("nan"),
        fp_per_1000km2=(fp / ocean_km2 * 1000.0) if ocean_km2 else float("nan"))


def record(result: EvalResult, detail: dict | None = None,
           db_path=None) -> None:
    """Append to the eval ledger. The ledger is the release gate's memory."""
    con = sqlite3.connect(str(db_path or CATALOG_DB))
    try:
        con.executescript(_EVAL_SCHEMA)
        con.execute(
            """INSERT INTO eval_runs (suite,pipeline_version,n_scenes,n_truth,
               n_pred,tp,fp,fn,precision,recall,f1,length_mae_m,
               fp_per_1000km2,detail_json,ran_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result.suite, PIPELINE_VERSION, result.n_scenes, result.n_truth,
             result.n_pred, result.tp, result.fp, result.fn, result.precision,
             result.recall, result.f1, result.length_mae_m,
             result.fp_per_1000km2, json.dumps(detail or {}), now_iso()))
        con.commit()
    finally:
        con.close()


def latest_runs(n: int = 10, db_path=None) -> list[dict]:
    con = sqlite3.connect(str(db_path or CATALOG_DB))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_EVAL_SCHEMA)
        rows = con.execute(
            "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def regression_check(result: EvalResult, db_path=None,
                     tolerance: float = 0.02) -> tuple[bool, str]:
    """Release gate: fail if F1 drops more than `tolerance` below the best
    prior run of the same suite. 'Every model change runs the harness.
    No exceptions.'"""
    prior = [r for r in latest_runs(100, db_path) if r["suite"] == result.suite]
    if not prior:
        return True, "first run for suite — baseline established"
    best = max(r["f1"] for r in prior)
    if result.f1 + tolerance < best:
        return False, f"REGRESSION: f1 {result.f1:.3f} vs prior best {best:.3f}"
    return True, f"ok: f1 {result.f1:.3f} (prior best {best:.3f})"
