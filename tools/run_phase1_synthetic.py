"""Phase 1 end-to-end exercise against the synthetic AOI suite.

1. Generate the 9-scene suite (7 train / 2 held out; coastal, offshore,
   rig-field regimes).
2. Build a candidate training set: run CFAR at a loose operating point on
   the train scenes, label each candidate by gate-radius proximity to a
   truth ship (candidates on rigs are dropped from training — they are
   'exclusion' class per the xView3 convention).
3. Train the v2 discriminator, save the versioned model artifact.
4. Run the *full automatic pipeline* (process_scene) on the held-out
   scenes: landmask → CFAR → discriminator → conformed DETECTION parquet
   → catalog PUBLISHED.
5. Run the harness on held-out scenes, record to the eval ledger, run the
   regression gate.
6. Dump `data/phase1_snapshot.json` (+ PNG renders) for the inspection
   dashboard.

On the deploy host the same script structure points at xView3 + the ~20
hand-labeled AOI scenes; only the scene source changes.
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.detect import cfar, classifier, landmask, pipeline
from maritime_isr.detect.scene import load_npz
from maritime_isr.eval import harness
from tools.make_synthetic_scene import SUITE, main as gen_scenes

SCENE_DIR = Path("data/synthetic_scenes")
MODEL_PATH = Path("data/models/discriminator_v2.pkl")
SNAPSHOT = Path("data/phase1_snapshot.json")
HELD_OUT = {"SYN_HELDOUT_A", "SYN_HELDOUT_B"}
LOOSE = cfar.CfarParams(pfa=1e-5)   # training-time operating point: recall-heavy
TIGHT = cfar.CfarParams(pfa=1e-6)   # publish operating point


def _load(sid):
    scene = load_npz(SCENE_DIR / f"{sid}.npz")
    labels = json.loads((SCENE_DIR / f"{sid}.labels.json").read_text())
    return scene, labels


def build_training_set():
    X, y = [], []
    stats = {"pos": 0, "neg": 0, "dropped_exclusion": 0}
    for sid, *_ in SUITE:
        if sid in HELD_OUT:
            continue
        scene, labels = _load(sid)
        ocean = landmask.ocean_mask(scene)
        cands = cfar.detect(scene, ocean, LOOSE)
        for c in cands:
            pred = {"lat": c.lat, "lon": c.lon}
            if any(harness._dist_m(c.lat, c.lon, e["lat"], e["lon"]) < 300
                   for e in labels["exclusion"]):
                stats["dropped_exclusion"] += 1
                continue
            is_ship = any(harness._dist_m(c.lat, c.lon, t["lat"], t["lon"]) < 300
                          for t in labels["truth"])
            X.append(classifier.features(scene, c, cands))
            y.append(1 if is_ship else 0)
            stats["pos" if is_ship else "neg"] += 1
    return np.stack(X), np.array(y), stats


def render_png_b64(scene, kept, truth, max_px=520):
    """Downsampled log-scaled scene render with detection boxes — for the
    inspection dashboard. Deliberately ugly (visibility rule 1)."""
    from PIL import Image, ImageDraw
    img = scene.intensity
    k = max(1, img.shape[0] // max_px)
    small = img[::k, ::k]
    x = np.log10(np.maximum(small, 1e-6))
    lo, hi = np.percentile(x, 1), np.percentile(x, 99.5)
    g = np.clip((x - lo) / (hi - lo), 0, 1)
    im = Image.fromarray((g * 255).astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(im)
    for t in truth:
        r, c = t["row"] / k, t["col"] / k
        d.ellipse([c - 7, r - 7, c + 7, r + 7], outline=(80, 160, 255), width=1)
    for c_, s in kept:
        r, c = c_.row / k, c_.col / k
        d.rectangle([c - 5, r - 5, c + 5, r + 5], outline=(255, 60, 60), width=2)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def main():
    if not SCENE_DIR.exists():
        gen_scenes(str(SCENE_DIR))

    print("== building candidate training set (loose CFAR on train scenes)")
    X, y, tstats = build_training_set()
    print(f"   candidates: {tstats}")
    disc = classifier.train(X, y)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    disc.save(MODEL_PATH)
    print(f"   discriminator saved -> {MODEL_PATH} (version {disc.version})")

    print("== processing held-out scenes through the automatic pipeline")
    eval_scenes, panels, reports = [], [], []
    for sid in sorted(HELD_OUT):
        scene, labels = _load(sid)
        report = pipeline.process_scene(scene, disc, TIGHT)
        reports.append(report)
        kept, _, ocean = pipeline.detect_scene(scene, disc, TIGHT)
        pred = [{"lat": c.lat, "lon": c.lon, "length_m": c.length_m,
                 "score": s} for c, s in kept]
        # honor exclusion convention (rigs neither TP nor FP)
        from maritime_isr.eval.xview3 import filter_excluded
        pred = filter_excluded(pred, labels["exclusion"])
        eval_scenes.append({"pred": pred, "truth": labels["truth"],
                            "ocean_area_km2": report["ocean_area_km2"]})
        panels.append({"scene_id": sid,
                       "png_b64": render_png_b64(scene, kept, labels["truth"]),
                       "n_truth": len(labels["truth"]),
                       "n_published": len(kept),
                       "coastal": labels["coastal"]})
        print(f"   {sid}: {report['n_candidates']} candidates -> "
              f"{report['n_published']} published, "
              f"{report['latency_s']:.1f}s, ocean {report['ocean_area_km2']:.0f} km²")

    print("== harness (held-out synthetic suite)")
    res = harness.evaluate(eval_scenes, suite="synthetic_aoi")
    harness.record(res, detail={"train_stats": tstats,
                                "publish_threshold": pipeline.PUBLISH_THRESHOLD})
    ok, msg = harness.regression_check(res)
    print(f"   P={res.precision:.3f} R={res.recall:.3f} F1={res.f1:.3f} "
          f"lenMAE={res.length_mae_m:.1f}m FP/1000km²={res.fp_per_1000km2:.2f}")
    print(f"   regression gate: {msg}")
    if not ok:
        sys.exit(2)

    SNAPSHOT.write_text(json.dumps({
        "metrics": res.__dict__, "panels": panels, "reports": reports,
        "ledger": harness.latest_runs(10)}, indent=1))
    print(f"== snapshot -> {SNAPSHOT}")


if __name__ == "__main__":
    main()
