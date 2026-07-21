"""Phase 1 acceptance tests. Each maps to a roadmap 1.x requirement or to
a failure mode found and fixed during the build (those get regression
tests so they can never silently return).

Run:  python -m pytest tests/test_phase1.py -q
Requires the synthetic suite + trained model on disk (created by
`python tools/run_phase1_synthetic.py`); the fixture builds them if absent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maritime_isr.detect import cfar, classifier, landmask, pipeline  # noqa: E402
from maritime_isr.detect.scene import Sigma0Scene, load_npz  # noqa: E402
from maritime_isr.eval import harness, xview3  # noqa: E402
from maritime_isr.schemas import DETECTION  # noqa: E402

SCENE_DIR = ROOT / "data" / "synthetic_scenes"
MODEL = ROOT / "data" / "models" / "discriminator_v2.pkl"
SNAPSHOT = ROOT / "data" / "phase1_snapshot.json"


@pytest.fixture(scope="session", autouse=True)
def suite_on_disk():
    if not (MODEL.exists() and SNAPSHOT.exists()):
        subprocess.run([sys.executable, "tools/run_phase1_synthetic.py"],
                       cwd=ROOT, check=True)


def _mk_scene(seed=7, ships=(), size=800, lat0=16.5, lon0=66.5):
    """Minimal in-memory scene: gamma clutter + specified ships."""
    from tools.make_synthetic_scene import ENL, PIX_M, _paint_target
    rng = np.random.default_rng(seed)
    mu = 0.02
    img = rng.gamma(ENL, mu / ENL, (size, size))
    for (r, c, length, width, heading, contrast) in ships:
        _paint_target(img, r, c, length, width, heading, contrast * mu)
    dlat = -PIX_M / 111_320.0
    dlon = PIX_M / (111_320.0 * np.cos(np.deg2rad(lat0)))
    return Sigma0Scene("TEST", img.astype(np.float32), lat0, lon0, dlat,
                       dlon, PIX_M, datetime.now(timezone.utc))


# ---- 1.1 CFAR baseline ----------------------------------------------------

def test_cfar_detects_bright_ships():
    scene = _mk_scene(ships=[(200, 200, 120, 20, 30, 300),
                             (500, 600, 60, 12, 100, 150)])
    ocean = np.ones(scene.shape, dtype=bool)
    contacts = cfar.detect(scene, ocean)
    assert len(contacts) >= 2
    d0 = min(abs(c.row - 200) + abs(c.col - 200) for c in contacts)
    d1 = min(abs(c.row - 500) + abs(c.col - 600) for c in contacts)
    assert d0 < 6 and d1 < 6


def test_extended_target_does_not_self_mask():
    """Regression: 200 m+ low-contrast ships were missed because the guard
    window was smaller than the target — its own energy inflated the
    clutter estimate. Guard must exceed the largest expected target."""
    scene = _mk_scene(ships=[(400, 400, 220, 30, 45, 25)])  # big, dim (14 dB)
    ocean = np.ones(scene.shape, dtype=bool)
    contacts = cfar.detect(scene, ocean)
    assert any(abs(c.row - 400) < 15 and abs(c.col - 400) < 15
               for c in contacts), "extended low-contrast target self-masked"


def test_length_estimate_tracks_truth():
    scene = _mk_scene(ships=[(300, 300, 180, 25, 0, 400)])
    ocean = np.ones(scene.shape, dtype=bool)
    contacts = cfar.detect(scene, ocean)
    c = min(contacts, key=lambda c: abs(c.row - 300) + abs(c.col - 300))
    assert 100 <= c.length_m <= 280  # right order, honest tolerance


# ---- land masking (roadmap: where naive pipelines die) ---------------------

def test_land_mask_kills_land_contacts():
    scene, labels = _load_suite("SYN_COAST_PBR")
    from global_land_mask import globe
    ocean = landmask.ocean_mask(scene)
    contacts = cfar.detect(scene, ocean)
    on_land = [c for c in contacts if globe.is_land(c.lat, c.lon)]
    assert not on_land, f"{len(on_land)} contacts on land — mask leak"
    assert len(contacts) > 0  # and the ocean still yields contacts


def _load_suite(sid):
    scene = load_npz(SCENE_DIR / f"{sid}.npz")
    labels = json.loads((SCENE_DIR / f"{sid}.labels.json").read_text())
    return scene, labels


# ---- 1.2 discriminator ------------------------------------------------------

def test_ghost_pass_suppresses_azimuth_ambiguities():
    """Physics override: a candidate with a much brighter parent at the
    azimuth-ambiguity offset is capped at 0.05 regardless of the model."""
    from tools.make_synthetic_scene import GHOST_OFFSET_PX, GHOST_RATIO
    scene = _mk_scene(ships=[(150, 400, 150, 25, 20, 1500)])
    from tools.make_synthetic_scene import _paint_target
    _paint_target(scene.intensity, 150 + GHOST_OFFSET_PX, 400, 150, 25, 20,
                  1500 * 0.02 * GHOST_RATIO)
    ocean = np.ones(scene.shape, dtype=bool)
    contacts = cfar.detect(scene, ocean)
    disc = classifier.Discriminator.load(MODEL)
    scores = disc.score(scene, contacts)
    ghost_scores = [s for c, s in zip(contacts, scores)
                    if abs(c.row - (150 + GHOST_OFFSET_PX)) < 10]
    assert ghost_scores and max(ghost_scores) <= 0.05
    parent = [s for c, s in zip(contacts, scores) if abs(c.row - 150) < 10]
    assert parent and max(parent) > 0.5  # the real ship survives


# ---- automatic pipeline (1.4 exit: no human touch) --------------------------

def test_process_scene_end_to_end_conformed_and_provenanced():
    scene, _ = _load_suite("SYN_HELDOUT_A")
    disc = classifier.Discriminator.load(MODEL)
    report = pipeline.process_scene(scene, disc)
    tbl = pq.read_table(report["parquet"])
    assert tbl.schema.equals(DETECTION)          # lands in the Phase 0 schema
    assert report["n_published"] == tbl.num_rows > 0
    df = tbl.to_pandas()
    for col in ("source", "source_ref", "acquired_at", "ingested_at",
                "pipeline_version", "h3_cell"):
        assert df[col].notna().all(), f"provenance gap in {col}"
    assert (df["source_ref"] == scene.scene_id).all()
    from maritime_isr.config import PIPELINE_VERSION
    assert (df["pipeline_version"] == PIPELINE_VERSION).all()
    assert report["publish_threshold"] == pipeline.PUBLISH_THRESHOLD


def test_scene_to_publish_latency_within_budget():
    """Exit criterion: scene lands → contacts published within 2 h. The
    per-scene compute must be seconds, leaving the budget to download +
    SNAP calibration on the deploy host."""
    scene, _ = _load_suite("SYN_HELDOUT_B")
    disc = classifier.Discriminator.load(MODEL)
    report = pipeline.process_scene(scene, disc)
    assert report["latency_s"] < 120.0


# ---- 1.3 harness -----------------------------------------------------------

def test_harness_matching_is_global_not_greedy():
    """Two predictions near one truth: exactly one TP, one FP — no
    double-assignment."""
    pred = [{"lat": 10.0, "lon": 65.0}, {"lat": 10.0005, "lon": 65.0}]
    truth = [{"lat": 10.0001, "lon": 65.0}]
    pairs, fp, fn = harness.match_scene(pred, truth)
    assert len(pairs) == 1 and len(fp) == 1 and len(fn) == 0


def test_harness_metrics_known_case():
    scenes = [{"pred": [{"lat": 10, "lon": 65}, {"lat": 11, "lon": 66}],
               "truth": [{"lat": 10, "lon": 65}, {"lat": 12, "lon": 67}],
               "ocean_area_km2": 1000.0}]
    r = harness.evaluate(scenes, suite="unit")
    assert (r.tp, r.fp, r.fn) == (1, 1, 1)
    assert r.precision == 0.5 and r.recall == 0.5
    assert r.fp_per_1000km2 == 1.0


def test_regression_gate_fires(tmp_path):
    db = tmp_path / "ledger.sqlite"
    good = harness.evaluate([{"pred": [{"lat": 10, "lon": 65}],
                              "truth": [{"lat": 10, "lon": 65}],
                              "ocean_area_km2": 100}], suite="gate")
    harness.record(good, db_path=db)
    bad = harness.evaluate([{"pred": [{"lat": 11, "lon": 66}],
                             "truth": [{"lat": 10, "lon": 65}],
                             "ocean_area_km2": 100}], suite="gate")
    ok, msg = harness.regression_check(bad, db_path=db)
    assert not ok and "REGRESSION" in msg


# ---- acceptance numbers (1.4) ----------------------------------------------

def test_heldout_f1_meets_acceptance_floor():
    """≥0.75 F1 on the held-out suite (synthetic stand-in; the xView3 run
    on the deploy host reuses this exact harness and gate)."""
    snap = json.loads(SNAPSHOT.read_text())
    assert snap["metrics"]["f1"] >= 0.75
    assert snap["metrics"]["precision"] >= 0.75  # high precision first — always


def test_eval_ledger_persists_runs():
    runs = harness.latest_runs(100)   # persistence, not recency
    assert any(r["suite"] == "synthetic_aoi" for r in runs)
    assert all(r["pipeline_version"] for r in runs)


# ---- xView3 adapter ---------------------------------------------------------

def test_xview3_loader_and_exclusion(tmp_path):
    csv = tmp_path / "labels.csv"
    csv.write_text(
        "scene_id,detect_lat,detect_lon,vessel_length_m,is_vessel,confidence\n"
        "S1,10.0,65.0,120,True,HIGH\n"
        "S1,10.5,65.5,,False,HIGH\n"
        "S1,11.0,66.0,50,True,LOW\n")
    scenes = xview3.load_labels(csv)
    assert len(scenes["S1"]["truth"]) == 1
    assert len(scenes["S1"]["exclusion"]) == 2
    pred = [{"lat": 10.5, "lon": 65.5}, {"lat": 10.0, "lon": 65.0}]
    kept = xview3.filter_excluded(pred, scenes["S1"]["exclusion"])
    assert len(kept) == 1 and kept[0]["lat"] == 10.0
