"""Composite per-vessel risk scoring (roadmap 5.3).

Explainable by construction: the score is a transparent weighted sum of
named contributions, and `risk_score` returns the DECOMPOSITION alongside
the number. An unexplainable score is unsellable to both a navy and an
insurer, so there is deliberately no learned black box here — the weights
are policy, visible and tunable.

Contributions:
  anomaly_history    decayed sum of this vessel's confirmed/open anomaly
                     alerts (RISK_HALF_LIFE_DAYS), weighted by type
  sanction_proximity graph distance to a sanctioned entity through the
                     ownership chain (1 hop worse than 3)
  flag_opacity       flags-of-convenience + recent reflag activity
  fingerprint_dev    deviation from the vessel's own behavioral baseline
                     (placeholder: reflag/rename count as a proxy until the
                     Phase 4 fingerprint has a multi-window history)
"""
from __future__ import annotations

import math

from ..config import RISK_HALF_LIFE_DAYS, RISK_SANCTION_HOPS
from ..graph.rules import ownership_chains

# type weights: a confirmed dark rendezvous is worse than a port-risk flag
ANOMALY_WEIGHTS = {
    "dark_rendezvous": 1.0, "ais_spoofing": 0.9, "dark_vessel": 0.8,
    "identity_then_anomaly": 1.0, "loitering_sensitive": 0.7,
    "port_risk_propagation": 0.5,
}
# flags-of-convenience seed list (illustrative, not exhaustive)
FOC_FLAGS = {"PA", "LR", "MH", "KM", "MT", "CY"}

W = dict(anomaly=0.45, sanction=0.30, flag=0.15, fingerprint=0.10)


def _decayed(alert_ts: float, at: float, base: float) -> float:
    dt_days = max(0.0, at - alert_ts) / 86400.0
    return base * 0.5 ** (dt_days / RISK_HALF_LIFE_DAYS)


def _anomaly_component(store, vid: str, at: float) -> tuple[float, list[dict]]:
    contribs = []
    total = 0.0
    for a in store.alerts():
        if a["subject"] != vid or a["disposition"] == "dismiss":
            continue
        w = ANOMALY_WEIGHTS.get(a["anomaly_type"], 0.5)
        # confirmed alerts count full; open alerts discounted (unreviewed)
        review_mult = 1.0 if a["disposition"] == "confirm" else 0.6
        c = _decayed(a["ts"], at, w * (a["score"] or a["confidence"]) * review_mult)
        if c <= 0:
            continue
        total += c
        contribs.append(dict(kind="anomaly", detail=a["anomaly_type"],
                             disposition=a["disposition"],
                             contribution=round(c, 3)))
    # squash so a pile of weak alerts can't exceed a single strong signal
    return 1.0 - math.exp(-total), contribs


def _sanction_component(store, vid: str, at: float) -> tuple[float, list[dict]]:
    best, best_hops = 0.0, None
    contribs = []
    for org, path in ownership_chains(store, vid, at):
        sanc = store.edges(org, "sanctioned-under", as_of=at)
        if not sanc:
            continue
        hops = len(path)
        val = max(0.0, 1.0 - (hops - 1) / RISK_SANCTION_HOPS)
        if val > best:
            best, best_hops = val, hops
    if best > 0:
        contribs.append(dict(kind="sanction_proximity",
                            detail=f"{best_hops}-hop ownership to sanctioned entity",
                            contribution=round(best, 3)))
    return best, contribs


def _flag_component(store, vid: str, at: float) -> tuple[float, list[dict]]:
    contribs, score = [], 0.0
    flags = store.edges(vid, "flagged-to", history=True)
    current = [e for e in flags if e.t_end is None]
    reflags = len([e for e in flags if e.t_end is not None])
    foc = any(store.node(e.dst) and
              store.node(e.dst)["props"].get("code") in FOC_FLAGS
              for e in current)
    if foc:
        score += 0.5
        contribs.append(dict(kind="flag_opacity", detail="flag of convenience",
                            contribution=0.5))
    if reflags:
        add = min(0.5, 0.25 * reflags)
        score += add
        contribs.append(dict(kind="flag_opacity",
                            detail=f"{reflags} reflag(s) on record",
                            contribution=round(add, 3)))
    return min(1.0, score), contribs


def _fingerprint_component(store, vid: str, at: float) -> tuple[float, list[dict]]:
    node = store.node(vid) or {"props": {}}
    changes = store._con.execute(
        "SELECT COUNT(*) FROM events WHERE subject=? AND event_type="
        "'identity_changed'", (vid,)).fetchone()[0]
    if changes:
        v = min(1.0, 0.3 * changes)
        return v, [dict(kind="fingerprint_dev",
                        detail=f"{changes} identity change(s)",
                        contribution=round(v, 3))]
    return 0.0, []


def risk_score(store, vid: str, at: float | None = None) -> dict:
    """Composite risk in [0,1] with full decomposition. THE explainability
    contract: score == sum(weight * component), every component named."""
    import time
    at = time.time() if at is None else at
    a, ac = _anomaly_component(store, vid, at)
    s, sc = _sanction_component(store, vid, at)
    fl, flc = _flag_component(store, vid, at)
    fp, fpc = _fingerprint_component(store, vid, at)
    score = W["anomaly"] * a + W["sanction"] * s + W["flag"] * fl + \
        W["fingerprint"] * fp
    return dict(
        vessel=vid, risk_score=round(score, 4), as_of=at,
        components=dict(
            anomaly_history=dict(weight=W["anomaly"], value=round(a, 4),
                                 weighted=round(W["anomaly"] * a, 4)),
            sanction_proximity=dict(weight=W["sanction"], value=round(s, 4),
                                    weighted=round(W["sanction"] * s, 4)),
            flag_opacity=dict(weight=W["flag"], value=round(fl, 4),
                              weighted=round(W["flag"] * fl, 4)),
            fingerprint_deviation=dict(weight=W["fingerprint"], value=round(fp, 4),
                                       weighted=round(W["fingerprint"] * fp, 4))),
        evidence=ac + sc + flc + fpc)


def rank_vessels(store, at: float | None = None, top: int = 20) -> list[dict]:
    vids = [r[0] for r in store._con.execute(
        "SELECT node_id FROM nodes WHERE node_type='vessel'")]
    scored = [risk_score(store, v, at) for v in vids]
    return sorted(scored, key=lambda r: r["risk_score"], reverse=True)[:top]
