"""Phase 3 acceptance tests — micro-scenarios, fast, no 30-day feed.
The gate tests encode the two bugs this phase was paid for: anchor
staleness (mid-track scene times) and the length hard cut."""
import json

import numpy as np
import pandas as pd
import pytest

from maritime_isr import fusion
from maritime_isr.fusion.associate import associate_scene
from maritime_isr.fusion.dark import build_static_layer, dark_cascade
from maritime_isr.tracks.builder import build_tracks
from maritime_isr.tracks.coverage import CoverageModel, SatPassSchedule

T0 = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")


def _pos(mmsi, times_min, lats, lons, sog=10.0, receiver="ter:test"):
    return pd.DataFrame(dict(
        mmsi=mmsi, lat=lats, lon=lons, sog_kn=sog, cog_deg=45.0,
        heading_deg=45.0, nav_status=0, msg_type=1,
        ts=[T0 + pd.Timedelta(minutes=m) for m in times_min],
        h3_cell="", receiver=receiver, n_receipts=1))


def _lane(mmsi, n=60, step=3, lat0=15.0, lon0=65.0, d=0.01, receiver="ter:test"):
    return _pos(mmsi, [i * step for i in range(n)],
                [lat0 + i * d for i in range(n)],
                [lon0 + i * d for i in range(n)], receiver=receiver)


def _scene(dets, t_min=90, sid="S1"):
    return dict(scene_id=sid, ts=str(T0 + pd.Timedelta(minutes=t_min)),
                detections=[dict(detection_id=f"d{k}", **d)
                            for k, d in enumerate(dets)])


def _sched(hours=24):
    w, t = [], T0.timestamp()
    while t < T0.timestamp() + hours * 3600:
        w.append((t, t + 11 * 60))
        t += 97 * 60
    return SatPassSchedule(w)


# ---------------- association ----------------

def test_mid_track_scene_time_gates():
    """Regression: anchor staleness, not track-end staleness. A scene time
    in the MIDDLE of a track must gate against it."""
    tracks, _ = build_tracks(_lane(100, n=100))          # track spans 300 min
    # true position at t=90 min is (15.30, 65.30); contact 700 m off
    sc = _scene([dict(lat=15.306, lon=65.30, length_m=180.0, score=0.9)], t_min=90)
    out = associate_scene(sc, tracks, {100: 180.0})
    assert out[0]["status"] == "matched" and out[0]["mmsi"] == 100


def test_stale_track_does_not_gate():
    tracks, _ = build_tracks(_lane(200, n=40))           # ends at 120 min
    sc = _scene([dict(lat=15.4, lon=65.4, length_m=180.0, score=0.9)],
                t_min=14 * 60)                            # 12 h+ later
    out = associate_scene(sc, tracks, {})
    assert out[0]["status"] == "unmatched"


def test_length_hard_cut():
    """A 60 m contact is not a 200 m merchant, however close."""
    tracks, _ = build_tracks(_lane(300, n=100))
    sc = _scene([dict(lat=15.30, lon=65.30, length_m=60.0, score=0.9)], t_min=90)
    out = associate_scene(sc, tracks, {300: 200.0})
    assert out[0]["status"] == "unmatched"


def test_global_assignment_no_double_assign():
    a = _lane(400, lat0=15.0)
    b = _lane(401, lat0=15.03)                            # ~3.3 km south lane
    tracks, _ = build_tracks(pd.concat([a, b]))
    sc = _scene([dict(lat=15.301, lon=65.30, length_m=100.0, score=.9),
                 dict(lat=15.331, lon=65.30, length_m=100.0, score=.9)], t_min=90)
    out = associate_scene(sc, tracks, {})
    got = {o["detection_id"]: o["mmsi"] for o in out}
    assert got["d0"] == 400 and got["d1"] == 401          # each to its own


def test_ambiguous_when_two_tracks_equally_plausible():
    a = _lane(500, lat0=15.000)
    b = _lane(501, lat0=15.004)                           # ~450 m apart
    tracks, _ = build_tracks(pd.concat([a, b]))
    sc = _scene([dict(lat=15.302, lon=65.30, length_m=100.0, score=.9)], t_min=90)
    out = associate_scene(sc, tracks, {})
    assert out[0]["status"] == "ambiguous"
    assert len(json.loads(out[0]["top_k"])) >= 2


def test_in_ais_gap_flag():
    tracks, _ = build_tracks(_lane(600, n=100))
    tid = tracks[0].track_id
    gaps = {tid: [dict(t_start=T0 + pd.Timedelta(minutes=80),
                       t_end=T0 + pd.Timedelta(minutes=100),
                       gap_type="INTENTIONAL_SILENCE")]}
    sc = _scene([dict(lat=15.306, lon=65.30, length_m=180.0, score=.9)], t_min=90)
    out = associate_scene(sc, tracks, {600: 180.0}, gaps)
    assert out[0]["status"] in ("matched", "ambiguous")
    assert out[0]["in_ais_gap"] is True
    assert out[0]["gap_type"] == "INTENTIONAL_SILENCE"


# ---------------- static layer & cascade ----------------

def _unm(k, lat, lon, day, length=60.0):
    # +3 h so the hearability lookback has sat passes behind it
    return dict(detection_id=f"u{k}", scene_id=f"SC{day}",
                ts=str(T0 + pd.Timedelta(days=day, hours=3)), lat=lat, lon=lon,
                length_m=length, score=0.9)


def test_static_layer_accumulates_and_rejects_spread():
    fixed = [_unm(k, 19.0000, 71.0000, d) for k, d in enumerate((0, 4, 9))]
    spread = [_unm(10 + k, 12.0 + k * 0.05, 64.0, d)      # 5 km apart — moving
              for k, d in enumerate((0, 4, 9))]
    objs = build_static_layer(fixed + spread)
    assert len(objs) == 1
    assert abs(objs[0]["lat"] - 19.0) < 0.001


def _cov_model(sat=True, schedule=None):
    # three vessels: the feed-health saturation needs realistic receipt
    # volume — a one-vessel ocean reads as a dying feed, correctly
    rx = "sat:spire" if sat else "ter:x"
    df = pd.concat([
        _lane(900, n=200, step=6, lat0=14.0, lon0=64.0, receiver=rx),
        _lane(901, n=200, step=6, lat0=16.0, lon0=66.0, receiver=rx),
        _lane(902, n=200, step=6, lat0=12.0, lon0=68.0, receiver=rx)])
    return CoverageModel(T0.timestamp(), schedule or _sched()).fit(df)


def test_cascade_dark_candidate():
    m = _cov_model()
    v = dark_cascade([_unm(0, 10.0, 63.0, 0, length=50.0)], m, [], [])
    assert v[0]["status"] == "dark_candidate"
    assert v[0]["dark_score"] >= 0.5


def test_cascade_static_before_coverage():
    m = _cov_model(sat=False, schedule=SatPassSchedule([]))   # deaf world
    statics = [dict(lat=10.0, lon=63.0, object_id="x")]
    v = dark_cascade([_unm(0, 10.0001, 63.0, 0)], m, statics, [])
    assert v[0]["status"] == "suppressed_static"              # not coverage


def test_cascade_coverage_when_feed_dead():
    m = _cov_model(sat=False, schedule=SatPassSchedule([]))
    v = dark_cascade([_unm(0, 10.0, 63.0, 0)], m, [], [])
    assert v[0]["status"] == "suppressed_coverage"


def test_cascade_size_floor():
    m = _cov_model()
    v = dark_cascade([_unm(0, 10.0, 63.0, 0, length=14.0)], m, [], [])
    assert v[0]["status"] == "suppressed_size"


def test_cascade_spoof_ambiguity():
    m = _cov_model()
    tracks, _ = build_tracks(_lane(950, n=200, step=6, lat0=10.0, lon0=63.0,
                                   d=0.001, receiver="sat:spire"))
    win = {950: [(T0.timestamp() - 3600, T0.timestamp() + 10 * 86400)]}
    v = dark_cascade([_unm(0, 10.02, 63.02, 0, length=50.0)], m, [], tracks, win)
    assert v[0]["status"] == "suppressed_spoof_ambiguity"


# ---------------- publish & eval ----------------

def test_run_fusion_publishes_with_provenance(tmp_path, monkeypatch):
    import maritime_isr.storage.conformed as conf
    import maritime_isr.storage.catalog as cat
    from maritime_isr import config
    monkeypatch.setattr(config, "CONFORMED_ROOT", tmp_path / "c")
    monkeypatch.setattr(conf, "CONFORMED_ROOT", tmp_path / "c")
    monkeypatch.setattr(config, "CATALOG_DB", tmp_path / "cat.sqlite")
    monkeypatch.setattr(cat, "CATALOG_DB", tmp_path / "cat.sqlite", raising=False)

    tracks, _ = build_tracks(_lane(970, n=100))
    m = _cov_model()
    sc = _scene([dict(lat=15.306, lon=65.30, length_m=180.0, score=.9),
                 dict(lat=10.0, lon=63.0, length_m=50.0, score=.9)], t_min=90)
    out = fusion.run_fusion([sc], tracks, m, {970: 180.0},
                            source_ref="t", partition_day="2026-06-01",
                            aoi="arabian_sea_v1")
    assert any(a["status"] == "matched" for a in out["associations"])
    assert any(v["status"] == "dark_candidate" for v in out["verdicts"])
    files = list((tmp_path / "c" / "dark_candidate").rglob("*.parquet"))
    assert files
    df = pd.read_parquet(files[0])
    for col in ("source", "source_ref", "pipeline_version", "h3_cell"):
        assert df[col].notna().all()


def test_fusion_eval_and_ledger(tmp_path):
    from maritime_isr.eval.fusion import evaluate_fusion, record_to_ledger
    from maritime_isr.eval.harness import latest_runs
    assoc = [dict(detection_id="d0", status="matched", mmsi=111,
                  ts=T0, in_ais_gap=False)]
    verd = [dict(detection_id="d1", status="dark_candidate", ts=T0)]
    truth = {"d0": "111", "d1": "ghost:x"}
    feed_truth = dict(dark_periods=[])
    r = evaluate_fusion(assoc, verd, truth, feed_truth,
                        ghost_lengths={"ghost:x": 50.0})
    assert r.assoc_accuracy == 1.0 and r.dark_precision == 1.0
    db = tmp_path / "l.sqlite"
    record_to_ledger(r, db_path=db)
    assert latest_runs(3, db_path=db)[0]["suite"] == "phase3_fusion_synthetic"
