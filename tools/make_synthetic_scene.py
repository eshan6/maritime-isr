"""Synthetic Sentinel-1-like sigma0 scene generator with ground truth.

Purpose: the Phase 1 acceptance criteria must be *measured*, and the
sandbox has no egress to Copernicus or the xView3 mirror. So the eval
suite that runs here is synthetic-but-physical: gamma-distributed sea
clutter (ENL≈4.4 for IW GRD), a smooth multiplicative wind field, ships as
oriented superellipse targets with realistic contrast and lengths,
azimuth-ambiguity ghosts as dim along-track replicas of the brightest
targets, fixed infrastructure at repeatable positions, and — critically —
scenes placed at real Arabian Sea coordinates so the *real* GLOBE land
mask is exercised (the Porbandar coastal scene contains actual Gujarat
land, painted bright, exactly where the mask says land is).

Truth convention mirrors xView3: ships are truth; ghosts are implicit
false targets; fixed infrastructure goes in an `exclusion` list (not TP,
not FP) because the static-object layer that absorbs it is a Phase 3
deliverable, not a Phase 1 one.

Same suite runs on the deploy host next to the xView3 holdout — two rows
in the same eval ledger.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

PIX_M = 10.0
ENL = 4.4
GHOST_OFFSET_PX = 480          # ≈4.8 km along azimuth
GHOST_RATIO = 0.035            # ≈ -14.6 dB replica

# fixed infrastructure (rigs/platforms) — same lat/lon in every offshore
# scene generation, which is exactly how the Phase 3 static layer will
# learn to absorb them
RIGS_OFFSHORE = [(19.62, 71.31), (19.55, 71.44)]


def _paint_target(img, r, c, length_m, width_m, heading_deg, amp):
    L = length_m / PIX_M / 2.0
    W = max(width_m / PIX_M / 2.0, 0.7)
    th = np.deg2rad(heading_deg)
    half = int(max(L, W) * 2) + 3
    r0, r1 = max(0, int(r) - half), min(img.shape[0], int(r) + half)
    c0, c1 = max(0, int(c) - half), min(img.shape[1], int(c) + half)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    dr, dc = rr - r, cc - c
    u = dr * np.cos(th) + dc * np.sin(th)
    v = -dr * np.sin(th) + dc * np.cos(th)
    kernel = np.exp(-(((u / L) ** 4) + ((v / W) ** 4)))
    img[r0:r1, c0:c1] += amp * kernel


def make_scene(scene_id: str, lat0: float, lon0: float, *, size: int = 1200,
               n_ships: int = 12, seed: int = 0, coastal: bool = False,
               acquired_at: datetime | None = None,
               include_rigs: bool = False) -> tuple[dict, dict]:
    rng = np.random.default_rng(seed)
    dlat, dlon = -PIX_M / 111_320.0, PIX_M / (111_320.0 * np.cos(np.deg2rad(lat0)))

    clutter_mu = 0.02  # typical ocean sigma0 (linear), moderate sea state
    wind = np.clip(gaussian_filter(rng.normal(1.0, 0.45, (size, size)), 40), 0.45, 1.9)
    img = rng.gamma(ENL, clutter_mu / ENL, (size, size)) * wind

    # real land, painted where GLOBE says land is (coastal scenes)
    from global_land_mask import globe
    step = 4
    la = lat0 + np.arange(0, size, step) * dlat
    lo = lon0 + np.arange(0, size, step) * dlon
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    land_c = globe.is_land(LA, LO)
    land = np.repeat(np.repeat(land_c, step, 0), step, 1)[:size, :size]
    if land.any():
        img[land] = rng.gamma(ENL, 0.25 / ENL, int(land.sum())) * \
            (1.0 + 2.0 * rng.random(int(land.sum())))

    truth, exclusion = [], []

    def ocean_spot():
        for _ in range(400):
            r = rng.integers(60, size - 60)
            c = rng.integers(60, size - 60)
            if not land[max(0, r - 12):r + 12, max(0, c - 12):c + 12].any():
                return int(r), int(c)
        raise RuntimeError("no ocean room")

    ghosts_from = []
    for _ in range(n_ships):
        r, c = ocean_spot()
        length = float(rng.uniform(28, 240))
        width = float(np.clip(length * rng.uniform(0.12, 0.2), 6, 35))
        heading = float(rng.uniform(0, 180))
        # log-uniform 15..2000 linear (≈12..33 dB) — real S1 ship contrasts
        contrast = float(10 ** rng.uniform(np.log10(15), np.log10(2000)))
        amp = contrast * clutter_mu
        _paint_target(img, r, c, length, width, heading, amp)
        lat, lon = lat0 + r * dlat, lon0 + c * dlon
        truth.append({"lat": float(lat), "lon": float(lon),
                      "length_m": length, "row": r, "col": c,
                      "contrast": contrast})
        if contrast > 400:   # only very bright targets spawn visible ghosts
            ghosts_from.append((r, c, length, width, heading, amp))

    # breaking-wave / whitecap clutter spikes: small, bright, shapeless —
    # the honest sea-clutter FP source the discriminator must learn to kill
    n_spikes = int(rng.integers(6, 12))
    for _ in range(n_spikes):
        r, c = ocean_spot()
        _paint_target(img, r, c, float(rng.uniform(12, 30)),
                      float(rng.uniform(10, 22)), float(rng.uniform(0, 180)),
                      float(rng.uniform(14, 40)) * clutter_mu)

    # azimuth-ambiguity ghosts: dim along-track replicas of bright targets
    n_ghosts = 0
    for (r, c, length, width, heading, amp) in ghosts_from:
        gr = r + GHOST_OFFSET_PX * (1 if r < size / 2 else -1)
        if 20 < gr < size - 20 and not land[int(gr), c]:
            _paint_target(img, gr, c, length, width, heading, amp * GHOST_RATIO)
            n_ghosts += 1

    if include_rigs:
        for rl_lat, rl_lon in RIGS_OFFSHORE:
            rr = (rl_lat - lat0) / dlat
            cc = (rl_lon - lon0) / dlon
            if 20 < rr < size - 20 and 20 < cc < size - 20:
                _paint_target(img, rr, cc, 60, 60, 0, 180 * clutter_mu)
                exclusion.append({"lat": rl_lat, "lon": rl_lon,
                                  "length_m": 60.0})

    acq = acquired_at or datetime.now(timezone.utc)
    scene = {"scene_id": scene_id, "intensity": img.astype(np.float32),
             "lat0": lat0, "lon0": lon0, "dlat": dlat, "dlon": dlon,
             "pixel_spacing_m": PIX_M, "acquired_at": acq.isoformat()}
    labels = {"scene_id": scene_id, "truth": truth, "exclusion": exclusion,
              "n_ghosts": n_ghosts, "coastal": coastal, "seed": seed}
    return scene, labels


def save(scene: dict, labels: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / f"{scene['scene_id']}.npz", **scene)
    (out_dir / f"{scene['scene_id']}.labels.json").write_text(
        json.dumps(labels, indent=1))


SUITE = [  # (scene_id, lat0, lon0, coastal, rigs) — real AOI coordinates
    ("SYN_OFFSHORE_A", 18.90, 66.50, False, False),
    ("SYN_OFFSHORE_B", 15.40, 68.20, False, False),
    ("SYN_OFFSHORE_C", 12.10, 71.00, False, False),
    ("SYN_RIGFIELD_A", 19.70, 71.25, False, True),
    ("SYN_RIGFIELD_B", 19.70, 71.25, False, True),
    ("SYN_COAST_PBR",  21.70, 69.55, True,  False),   # Porbandar coast — real land
    ("SYN_COAST_MUM",  19.05, 72.70, True,  False),   # Mumbai approaches — real land
    ("SYN_HELDOUT_A",  16.80, 67.40, False, True),
    ("SYN_HELDOUT_B",  21.62, 69.48, True,  False),   # held-out coastal
]


def main(out="data/synthetic_scenes"):
    out_dir = Path(out)
    t = datetime(2026, 7, 12, 1, 5, tzinfo=timezone.utc)
    for i, (sid, la, lo, coastal, rigs) in enumerate(SUITE):
        scene, labels = make_scene(sid, la, lo, seed=100 + i, coastal=coastal,
                                   include_rigs=rigs,
                                   acquired_at=t + timedelta(days=i))
        save(scene, labels, out_dir)
        print(f"{sid}: {len(labels['truth'])} ships, "
              f"{labels['n_ghosts']} ghosts, coastal={coastal}")


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:])
