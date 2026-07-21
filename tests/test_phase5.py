"""Phase 5 acceptance tests. The load-bearing ones: precision gating drops
sub-threshold candidates, disposition ledger captures labels, feedback
retune produces a measured non-negative precision delta, and every risk
score equals the sum of its named components (the explainability contract)."""
import pandas as pd
import pytest

from maritime_isr import anomaly
from maritime_isr.anomaly.library import SENSITIVE_ZONES, run_anomaly_library
from maritime_isr.config import ANOMALY_THRESHOLDS
from maritime_isr.graph import (GraphStore, ensure_world, ingest_ownership,
                           ingest_sanctions, resolve_mmsi)
from maritime_isr.tracks.builder import build_tracks

T0 = pd.Timestamp("2026-06-15", tz="UTC")


@pytest.fixture
def g(tmp_path):
    s = GraphStore(tmp_path / "g.sqlite")
    ensure_world(s)
    yield s
    s.close()


def _track(mmsi, pts, step_min=6):
    df = pd.DataFrame([dict(mmsi=mmsi, lat=la, lon=lo, sog_kn=sg, cog_deg=90.0,
                            heading_deg=90.0, nav_status=0, msg_type=1,
                            ts=T0 + pd.Timedelta(minutes=step_min * i),
                            h3_cell="", receiver="ter:t", n_receipts=1)
                       for i, (la, lo, sg) in enumerate(pts)])
    built, _ = build_tracks(df)
    return built[0]


# ---------------- individual detectors ----------------

def test_dark_vessel_gated(g):
    verdicts = [
        dict(detection_id="d_hi", status="dark_candidate", ts=T0, lat=10.0,
             lon=63.0, length_m=50.0, dark_score=0.8, hearable_conf=0.9),
        dict(detection_id="d_lo", status="dark_candidate", ts=T0, lat=11.0,
             lon=63.0, length_m=25.0, dark_score=0.40, hearable_conf=0.9),
    ]
    fired = anomaly.detect_dark_vessels(g, verdicts, source_ref="t")
    assert len(fired) == 1                       # 0.40 < 0.50 gate, dropped
    a = g.alerts(anomaly_type="dark_vessel")[0]
    assert a["score"] == 0.8 and a["evidence"]


def test_spoofing_scales_with_separation(g):
    events = [dict(mmsi=111, event_type="DUPLICATE_MMSI",
                   t_start=T0, t_end=T0 + pd.Timedelta("1h"),
                   max_separation_km=1200.0, detail="x"),
              dict(mmsi=222, event_type="DUPLICATE_MMSI",
                   t_start=T0, t_end=T0 + pd.Timedelta("1h"),
                   max_separation_km=15.0, detail="y")]
    fired = anomaly.detect_spoofing(g, events, [], source_ref="t")
    # 1200 km is unambiguous; 15 km (0.55+0.0075=0.5575) also clears .55 gate
    assert len(fired) >= 1
    top = max(g.alerts(anomaly_type="ais_spoofing"), key=lambda a: a["score"])
    assert top["props"]["mmsi"] == 111


def test_sensitive_loitering(g):
    z = SENSITIVE_ZONES[0]
    tr = _track(419800001, [(z["lat"] + 0.01, z["lon"], 0.4)] * 40)
    fired = anomaly.detect_sensitive_loitering(g, [tr], source_ref="t")
    assert len(fired) == 1
    assert g.alerts(anomaly_type="loitering_sensitive")[0]["props"]["zone"] == z["name"]


def test_loitering_outside_zone_silent(g):
    tr = _track(419800009, [(5.5, 61.0, 0.4)] * 40)      # empty ocean corner
    assert anomaly.detect_sensitive_loitering(g, [tr], source_ref="t") == []


def test_port_risk_propagation(g):
    tr = _track(419800002, [(24.79, 66.98, 0.3)] * 30)   # berthed at Karachi
    fired = anomaly.detect_port_risk(g, [tr], source_ref="t")
    assert len(fired) == 1
    assert "Karachi" in g.alerts(anomaly_type="port_risk_propagation")[0]["props"]["ports"]


# ---------------- disposition + feedback ----------------

def test_disposition_workflow(g):
    g.upsert_node("detection:x", "detection")
    g.add_alert("a1", "dark_vessel", "detection:x", T0.timestamp(), 0.8,
                [{"edge": "e"}], anomaly_type="dark_vessel", score=0.8)
    with pytest.raises(ValueError):
        g.dispose("a1", "banana")
    g.dispose("a1", "confirm", analyst="cdr_smith")
    assert g.alerts()[0]["disposition"] == "confirm"
    d = g.dispositions("dark_vessel")
    assert len(d) == 1 and d[0]["label"] == "confirm" and d[0]["analyst"] == "cdr_smith"


def test_feedback_retune_improves_precision(g):
    # 8 alerts: high-scoring ones true, low-scoring ones false → a higher
    # threshold should raise precision, the whole point of the loop
    for i in range(8):
        score = 0.5 + 0.05 * i
        true = score >= 0.7
        aid = f"a{i}"
        g.upsert_node(f"detection:d{i}", "detection")
        g.add_alert(aid, "dark_vessel", f"detection:d{i}", T0.timestamp(),
                    score, [{"edge": "e"}], anomaly_type="dark_vessel", score=score)
        g.dispose(aid, "confirm" if true else "dismiss")
    r = anomaly.propose_retune(g, "dark_vessel", apply=False)
    assert r is not None
    assert r.precision_after >= r.precision_before
    assert r.precision_delta >= 0
    assert r.new_threshold >= r.old_threshold      # tightened


def test_feedback_needs_minimum_dispositions(g):
    g.upsert_node("detection:z", "detection")
    g.add_alert("a", "dark_vessel", "detection:z", T0.timestamp(), 0.8,
                [{"edge": "e"}], anomaly_type="dark_vessel", score=0.8)
    g.dispose("a", "confirm")
    assert anomaly.propose_retune(g, "dark_vessel") is None   # 1 < min gate


# ---------------- risk scoring ----------------

def _sanctioned_world(g):
    ingest_ownership(g, dict(
        organizations=[dict(name="BadCo", jurisdiction="XX")],
        vessel_owners=[dict(mmsi=419100002, org="BadCo")]),
        source_ref="t", as_of=T0.timestamp())
    ingest_sanctions(g, [dict(registry="OFAC", entry_id="S", name="BadCo",
                              entry_type="entity", program="P",
                              valid_from_epoch=T0.timestamp() - 30 * 86400,
                              valid_to_epoch=None)], source_ref="t")


def test_risk_decomposes_exactly(g):
    _sanctioned_world(g)
    vid = resolve_mmsi(g, 419100002, at=T0.timestamp())
    rs = anomaly.risk_score(g, vid, at=T0.timestamp())
    total = sum(c["weighted"] for c in rs["components"].values())
    assert abs(rs["risk_score"] - total) < 1e-9      # the explainability contract
    assert rs["evidence"]                             # named contributions present


def test_risk_ordering_sanctioned_over_clean(g):
    _sanctioned_world(g)
    g.upsert_node("vessel:imo:clean", "vessel", dict(mmsi=999))
    hi = anomaly.risk_score(g, resolve_mmsi(g, 419100002, at=T0.timestamp()),
                            at=T0.timestamp())
    lo = anomaly.risk_score(g, "vessel:imo:clean", at=T0.timestamp())
    assert hi["risk_score"] > lo["risk_score"]


def test_eval_and_ledger(g, tmp_path):
    from maritime_isr.eval.anomaly import evaluate_anomalies, record_to_ledger
    from maritime_isr.eval.harness import latest_runs
    _sanctioned_world(g)
    hi = anomaly.risk_score(g, resolve_mmsi(g, 419100002, at=T0.timestamp()),
                            at=T0.timestamp())
    g.upsert_node("vessel:imo:clean", "vessel")
    lo = anomaly.risk_score(g, "vessel:imo:clean", at=T0.timestamp())
    fired = {k: [] for k in ANOMALY_THRESHOLDS}
    fired["dark_vessel"] = ["x"]
    r = evaluate_anomalies(g, fired, None, hi, lo)
    assert r.risk_decomposes and r.risk_ordering_correct
    db = tmp_path / "l.sqlite"
    record_to_ledger(r, db_path=db)
    assert latest_runs(3, db_path=db)[0]["suite"] == "phase5_anomaly_synthetic"
