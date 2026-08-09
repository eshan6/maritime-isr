"""Phase 2 acceptance tests. Micro-scenarios constructed inline — fast,
deterministic, no dependence on the 30-day feed (that's the eval suite's
job, run via tools/run_phase2_synthetic.py and gated in the ledger)."""
import math

import numpy as np
import pandas as pd
import pytest

from maritime_isr import tracks as trk
from maritime_isr.config import MAX_FEASIBLE_SPEED_KN
from maritime_isr.tracks.builder import build_tracks
from maritime_isr.tracks.coverage import CoverageModel, SatPassSchedule, classify_gaps
from maritime_isr.tracks.features import detect_encounters, extract_features
from maritime_isr.tracks.kalman import KN_TO_MS, epoch_s, filter_smooth

T0 = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")


def _pos(mmsi, times_min, lats, lons, sog=10.0, receiver="ter:test"):
    n = len(times_min)
    return pd.DataFrame(dict(
        mmsi=mmsi, lat=lats, lon=lons, sog_kn=sog, cog_deg=45.0,
        heading_deg=45.0, nav_status=0, msg_type=1,
        ts=[T0 + pd.Timedelta(minutes=m) for m in times_min],
        h3_cell="", receiver=receiver, n_receipts=1))


def _lane(mmsi, n=40, step_min=3, lat0=15.0, lon0=65.0, dlat=0.01,
          receiver="ter:test"):
    return _pos(mmsi, [i * step_min for i in range(n)],
                [lat0 + i * dlat for i in range(n)],
                [lon0 + i * dlat for i in range(n)], receiver=receiver)


# ---------------- kalman ----------------

def test_uncertainty_grows_and_is_speed_capped():
    df = _lane(100)
    tracks, _ = build_tracks(df)
    st = tracks[0].states[-1]
    r1 = st.uncertainty_radius_m(st.t + 600)
    r2 = st.uncertainty_radius_m(st.t + 7200)
    assert r2 > r1 > 0
    # after 10 h the cap must dominate: exactly the 60 kn cone
    dt = 36000
    assert st.uncertainty_radius_m(st.t + dt) <= MAX_FEASIBLE_SPEED_KN * KN_TO_MS * dt + 1


def test_smoother_reduces_noise():
    rng = np.random.default_rng(3)
    n = 60
    t = np.arange(n) * 180.0
    true_lat = 15 + np.arange(n) * 0.005
    true_lon = 65 + np.arange(n) * 0.005
    noisy_lat = true_lat + rng.normal(0, 0.0009, n)   # ~100 m noise
    noisy_lon = true_lon + rng.normal(0, 0.0009, n)
    states, _ = filter_smooth(t, noisy_lat, noisy_lon,
                              noisy=np.ones(n, bool))  # downweight path
    sm = np.array([s.latlon for s in states])
    rms_raw = np.sqrt(np.mean((noisy_lat - true_lat) ** 2))
    rms_sm = np.sqrt(np.mean((sm[:, 0] - true_lat) ** 2))
    assert rms_sm < rms_raw * 0.8


# ---------------- builder ----------------

def test_duplicate_mmsi_split_and_logged():
    a = _lane(200, lat0=15.0, lon0=65.0)
    b = _lane(200, lat0=22.0, lon0=74.0)   # same MMSI, 1000+ km away
    tracks, spoofs = build_tracks(pd.concat([a, b]))
    assert len(tracks) == 2
    dups = [s for s in spoofs if s["event_type"] == "DUPLICATE_MMSI"]
    assert len(dups) == 1                   # merged into one episode
    assert dups[0]["max_separation_km"] > 500


def test_impossible_jump_isolated_not_dropped():
    df = _lane(300, n=30)
    jump = _pos(300, [45], [24.0], [75.0])  # one report, ~1300 km off
    tracks, spoofs = build_tracks(pd.concat([df, jump]).sort_values("ts"))
    assert len(tracks) == 1
    pts = tracks[0].points
    assert (pts.quality == "outlier").sum() == 1     # kept, flagged
    assert any(s["event_type"] == "IMPOSSIBLE_KINEMATICS" for s in spoofs)


def test_mmsi_reuse_breaks_track_with_lineage():
    early = _lane(400, n=20)
    late = _pos(400, [20 * 3 + 9 * 24 * 60 + i * 3 for i in range(20)],
                [15.5 + i * 0.01 for i in range(20)],
                [65.5 + i * 0.01 for i in range(20)])   # 9 days later
    tracks, _ = build_tracks(pd.concat([early, late]))
    assert len(tracks) == 2
    t2 = max(tracks, key=lambda t: t.points["ts"].min())
    t1 = min(tracks, key=lambda t: t.points["ts"].min())
    assert t2.fragmented_from == t1.track_id


# ---------------- coverage & gaps ----------------

def _sched(period_min=97, pass_min=11, hours=48):
    w = []
    t = T0.timestamp()
    end = t + hours * 3600
    while t < end:
        w.append((t, t + pass_min * 60))
        t += period_min * 60
    return SatPassSchedule(w)


def test_ratio_coverage_low_at_sat_only_area():
    # neighborhood where every vessel is heard by satellite only
    df = _lane(500, receiver="sat:spire")
    m = CoverageModel(T0.timestamp(), _sched()).fit(df)
    p_ter, _, _ = m.p_heard(15.05, 65.05, T0.timestamp() + 3600)
    assert p_ter < 0.2


def test_gap_intentional_when_covered_and_passes_missed():
    # vessel sat-heard, silent 6 h through many passes
    times = list(range(0, 120, 6)) + list(range(480, 600, 6))
    lats = [14 + 0.005 * i for i in range(len(times))]
    lons = [64 + 0.005 * i for i in range(len(times))]
    df = _pos(600, times, lats, lons, receiver="sat:spire")
    tracks, _ = build_tracks(df)
    gaps = classify_gaps(tracks[0], CoverageModel(T0.timestamp(), _sched()).fit(df))
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "INTENTIONAL_SILENCE"
    assert gaps[0]["confidence"] > 0.6


def test_gap_sat_pass_between_passes():
    # 80-min silence, fits between 97-min-period passes (0 full passes inside)
    times = list(range(0, 60, 6)) + list(range(140, 200, 6))
    df = _pos(700, times, [14.0] * len(times), [64.0 + 0.001 * i for i in range(len(times))],
              receiver="sat:spire")
    tracks, _ = build_tracks(df)
    sched = SatPassSchedule([(T0.timestamp() - 500, T0.timestamp() + 55 * 60),
                             (T0.timestamp() + 145 * 60, T0.timestamp() + 200 * 60)])
    gaps = classify_gaps(tracks[0], CoverageModel(T0.timestamp(), sched).fit(df))
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "SAT_PASS_GAP"


def test_gap_coverage_when_no_evidence():
    # silence in an area with zero receipts from anyone, no sat schedule
    times = list(range(0, 60, 6)) + list(range(300, 360, 6))
    df = _pos(800, times, [8.0] * len(times), [62.0 + 0.001 * i for i in range(len(times))],
              receiver="ter:far")
    tracks, _ = build_tracks(df)
    # model fit on a DIFFERENT region — no evidence near the gap path
    other = _lane(801, lat0=22.0, lon0=75.0)
    gaps = classify_gaps(tracks[0], CoverageModel(T0.timestamp()).fit(other))
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "COVERAGE_GAP"


def test_no_gaps_on_nominal_cadence():
    df = _lane(900, n=100)
    tracks, _ = build_tracks(df)
    assert classify_gaps(tracks[0], CoverageModel(T0.timestamp()).fit(df)) == []


def test_spoof_window_suppresses_intentional():
    times = list(range(0, 120, 6)) + list(range(480, 600, 6))
    df = _pos(1000, times, [14 + 0.005 * i for i in range(len(times))],
              [64 + 0.005 * i for i in range(len(times))], receiver="sat:spire")
    tracks, _ = build_tracks(df)
    model = CoverageModel(T0.timestamp(), _sched()).fit(df)
    win = [(T0.timestamp(), T0.timestamp() + 700 * 60)]
    gaps = classify_gaps(tracks[0], model, spoof_windows=win)
    assert gaps[0]["gap_type"] != "INTENTIONAL_SILENCE"


# ---------------- encounters & features ----------------

def test_encounter_detected_and_fast_crossing_rejected():
    # meeting: two vessels ~220 m apart at 0.5 kn for 40 min
    mins = list(range(0, 120, 4))
    a = _pos(1100, mins, [18.0] * len(mins), [70.0] * len(mins), sog=0.5)
    b = _pos(1101, mins, [18.002] * len(mins), [70.0] * len(mins), sog=0.5)
    # crossing: two vessels pass within 300 m at 14 kn
    c = _pos(1102, mins, [19.0 + 0.014 * m for m in mins], [71.0] * len(mins), sog=14)
    d = _pos(1103, mins, [19.0 + 0.014 * (mins[-1] - m) for m in mins],
             [71.002] * len(mins), sog=14)
    tracks, _ = build_tracks(pd.concat([a, b, c, d]))
    encs = detect_encounters(tracks)
    pairs = {frozenset((e["mmsi_a"], e["mmsi_b"])) for e in encs}
    assert frozenset((1100, 1101)) in pairs
    assert frozenset((1102, 1103)) not in pairs


def test_loitering_and_port_calls():
    # vessel A: 3 h stationary offshore -> loiter episode
    mins = list(range(0, 200, 6))
    a = _pos(1200, mins, [19.5] * len(mins), [67.5] * len(mins), sog=0.4)
    ta, _ = build_tracks(a)
    fa = extract_features(ta[0])
    assert fa["n_loiter_episodes"] >= 1
    # vessel B: berthed inside Porbandar radius -> port call, NO loiter
    b = _pos(1201, mins, [21.63] * len(mins), [69.60] * len(mins), sog=0.2)
    tb, _ = build_tracks(b)
    fb = extract_features(tb[0])
    assert "Porbandar" in fb["port_calls"]
    assert fb["n_loiter_episodes"] == 0    # in-port stillness is a berth


def test_queueing_at_an_anchorage_is_not_loitering():
    """A vessel waiting at a designated anchorage is queueing, not loitering.

    The port layer alone did not cover this and the gap was expensive: a berth
    radius describes the berth, and a ship waiting for that berth is 15-30 km
    further out at the anchorage. Kandla's is 30 km from the Kandla berth
    coordinate, so `PORT_RADIUS_KM` at 8 km could never reach it however many
    ports were listed. Measured before the anchorage layer existed: 29 of 33
    `loitering_sensitive` alerts were ordinary merchants queueing at Kandla —
    about 12% precision against ADR-004's 70% floor.
    """
    mins = list(range(0, 200, 6))
    a = _pos(1210, mins, [22.80] * len(mins), [70.05] * len(mins), sog=0.3)
    fa = extract_features(build_tracks(a)[0][0])
    assert fa["n_loiter_episodes"] == 0, (
        "a vessel stopped at the Kandla anchorage was called a loiterer")


def test_the_loitering_rule_still_fires_away_from_a_waiting_area():
    """Suppression is targeted, not a blanket kill.

    This is the other half of the check above, and it is the one that matters:
    silencing a rule everywhere would also show as "no false positives". A
    stopped vessel in the middle of the Mumbai High field — 130 km from the
    nearest anchorage, inside a sensitive geofence — must still be an episode.
    """
    from maritime_isr.tracks.features import AOI_ANCHORAGES, AOI_PORTS, _hav_m

    mins = list(range(0, 200, 6))
    lat, lon = 19.30, 71.30                       # Mumbai High oil field
    nearest = min(_hav_m(lat, lon, pla, plo)
                  for pla, plo in [*AOI_PORTS.values(), *AOI_ANCHORAGES.values()])
    assert nearest > 50_000, (
        "fixture is meant to be well clear of every port and anchorage")

    a = _pos(1211, mins, [lat] * len(mins), [lon] * len(mins), sog=0.3)
    fa = extract_features(build_tracks(a)[0][0])
    assert fa["n_loiter_episodes"] >= 1, (
        "the anchorage suppression silenced loitering everywhere, not just "
        "in the waiting areas")


# ---------------- publish & eval ----------------

def test_run_track_engine_publishes_with_provenance(tmp_path, monkeypatch):
    import maritime_isr.storage.conformed as conf
    import maritime_isr.storage.catalog as cat
    from maritime_isr import config
    monkeypatch.setattr(config, "CONFORMED_ROOT", tmp_path / "conformed")
    monkeypatch.setattr(conf, "CONFORMED_ROOT", tmp_path / "conformed")
    monkeypatch.setattr(config, "CATALOG_DB", tmp_path / "cat.sqlite")
    monkeypatch.setattr(cat, "CATALOG_DB", tmp_path / "cat.sqlite", raising=False)

    df = _lane(1300, n=50)
    out = trk.run_track_engine(df, source_ref="test", partition_day="2026-06-01",
                               aoi="arabian_sea_v1")
    assert len(out["tracks"]) == 1
    tp = list((tmp_path / "conformed" / "track_point").rglob("*.parquet"))
    assert tp, "track_point parquet not published"
    pts = pd.read_parquet(tp[0])
    for col in ("source", "source_ref", "pipeline_version", "h3_cell"):
        assert col in pts.columns and pts[col].notna().all()


def test_eval_and_ledger(tmp_path):
    from maritime_isr.eval.tracks import evaluate_tracks, record_to_ledger
    from maritime_isr.eval.harness import latest_runs

    df_a = _pos(1400, list(range(0, 120, 4)), [18.0] * 30, [70.0] * 30, sog=0.5)
    df_b = _pos(1401, list(range(0, 120, 4)), [18.002] * 30, [70.0] * 30, sog=0.5)
    tracks, _ = build_tracks(pd.concat([df_a, df_b]))
    encs = detect_encounters(tracks)
    truth = dict(
        vessel_segments=[dict(mmsi=1400, t0=str(T0), t1=str(T0 + pd.Timedelta("2h"))),
                         dict(mmsi=1401, t0=str(T0), t1=str(T0 + pd.Timedelta("2h")))],
        dark_periods=[], spoof=[],
        encounters=[dict(mmsi_a=1400, mmsi_b=1401, t0=str(T0),
                         t1=str(T0 + pd.Timedelta("2h")))],
        negatives=[])
    r = evaluate_tracks(tracks, [], encs, truth)
    assert r.fragmentation_rate == 0.0
    assert r.encounter_precision == 1.0 and r.encounter_recall == 1.0
    db = tmp_path / "eval.sqlite"
    record_to_ledger(r, db_path=db)
    rows = latest_runs(5, db_path=db)
    assert rows and rows[0]["suite"] == "phase2_tracks_synthetic"
