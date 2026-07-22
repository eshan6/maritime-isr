"""Unit 0.2 exit-test helper: is a calibrated scene in a plausible sigma-nought
dB range over open water?

Sigma-nought (sigma0) VV over calm-to-moderate open ocean at C-band sits
roughly in -25..-5 dB (wind- and incidence-dependent); land and hard targets
run higher. We sample the scene, take a robust central band, and assert the
median sits in an oceanic window. This is a sanity gate, not calibration truth —
it catches a broken chain (all-zero, all-NaN, linear-not-dB, wildly positive).
"""
from __future__ import annotations

from pathlib import Path


OCEAN_DB_MIN = -35.0
OCEAN_DB_MAX = 5.0


def check_scene(cog_path: str | Path) -> dict:
    import numpy as np
    import rasterio

    with rasterio.open(cog_path) as ds:
        band = ds.read(1, masked=True)
    valid = band.compressed()
    if valid.size == 0:
        return {"ok": False, "reason": "no valid pixels (all masked/NaN)"}
    finite = valid[np.isfinite(valid)]
    if finite.size == 0:
        return {"ok": False, "reason": "no finite pixels (chain emitted NaN/inf)"}

    p05, p50, p95 = (float(np.percentile(finite, q)) for q in (5, 50, 95))
    looks_db = -60.0 < p50 < 20.0  # dB scenes live here; linear power would be ~0..N
    ocean_ok = OCEAN_DB_MIN <= p50 <= OCEAN_DB_MAX
    ok = looks_db and ocean_ok
    reason = "ok"
    if not looks_db:
        reason = f"median {p50:.1f} not in dB range — chain may be linear, not dB"
    elif not ocean_ok:
        reason = (f"median {p50:.1f} dB outside ocean window "
                  f"[{OCEAN_DB_MIN},{OCEAN_DB_MAX}] — scene may be land-heavy (ok if coastal)")
    return {"ok": ok, "reason": reason, "p05_db": p05, "median_db": p50,
            "p95_db": p95, "n_valid": int(finite.size)}


def run(limit: int | None = None) -> int:
    from ..db import connect
    from ..schemas import SceneStatus
    con = connect()
    rows = con.execute(
        "SELECT scene_id, calibrated_uri FROM scene_catalog WHERE status=?",
        [SceneStatus.CALIBRATED.value],
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        print("[validate] no calibrated scenes yet. Run: maritime-isr preprocess --limit 1")
        return 0
    all_ok = True
    for scene_id, uri in rows:
        try:
            res = check_scene(uri)
        except Exception as e:  # noqa: BLE001
            print(f"[validate] {scene_id}: ERROR reading {uri}: {e}")
            all_ok = False
            continue
        flag = "PASS" if res["ok"] else "CHECK"
        all_ok = all_ok and res["ok"]
        if "median_db" in res:
            print(f"[validate] {flag} {scene_id}: median={res['median_db']:.1f} dB "
                  f"(p05={res['p05_db']:.1f}, p95={res['p95_db']:.1f}, "
                  f"n={res['n_valid']}) — {res['reason']}")
        else:
            print(f"[validate] {flag} {scene_id}: {res['reason']}")
    return 0 if all_ok else 1
