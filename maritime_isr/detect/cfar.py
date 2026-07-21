"""Detector v1 — Cell-Averaging CFAR on calibrated sigma0 (roadmap 1.1).

Deliberately first because it is transparent and tunable: a per-pixel
adaptive threshold T = alpha * mu_train, where mu_train is the local sea
clutter mean estimated from a training ring (outer window minus guard
window, computed with box filters so the whole scene is O(N)), and alpha
is derived from the design false-alarm probability under the exponential
intensity model:

    alpha = N * (PFA^(-1/N) - 1),  N = training-cell count.

Exceedances are clustered into blobs (8-connectivity); each blob becomes a
contact candidate with position (intensity-weighted centroid → lat/lon),
backscatter statistics, and length/width estimated from the blob's second
moments — exactly the roadmap's contract for 1.1 output.

Physics note carried from the roadmap: at ~10–20 m resolution the floor
is ~15–25 m vessel length in favourable sea states; min_area_px encodes
that floor rather than pretending past it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter
from skimage.measure import label, regionprops


@dataclass
class CfarParams:
    pfa: float = 1e-6          # design false-alarm probability per pixel
    guard_px: int = 25         # guard edge (odd). MUST exceed the largest expected
                               # target (~250 m = 25 px), else extended targets leak
                               # energy into the training ring and self-mask —
                               # observed failure: 200 m+ low-contrast ships missed
    train_px: int = 51         # outer edge (odd); N=1976 training cells keeps the
                               # clutter-mean estimate tight at the same alpha
    min_area_px: int = 3       # detection floor: ~2-3 px ≈ 20-30 m target
    max_area_px: int = 4000    # larger than any ship → infrastructure/land leak


@dataclass
class Contact:
    row: float
    col: float
    lat: float
    lon: float
    area_px: int
    peak_sigma0: float
    mean_sigma0: float
    clutter_mu: float          # local clutter mean at detection time
    contrast_db: float         # 10log10(peak/clutter) — the SNR analysts feel
    length_m: float
    width_m: float
    orientation_deg: float
    bbox: tuple[int, int, int, int] = field(default=(0, 0, 0, 0))  # r0,c0,r1,c1


def _train_mean(img: np.ndarray, p: CfarParams) -> np.ndarray:
    """Local clutter mean over the training ring via two box sums."""
    outer_sum = uniform_filter(img, size=p.train_px, mode="reflect") * p.train_px**2
    guard_sum = uniform_filter(img, size=p.guard_px, mode="reflect") * p.guard_px**2
    n_train = p.train_px**2 - p.guard_px**2
    return np.maximum((outer_sum - guard_sum) / n_train, 1e-12)


def detect(scene, ocean: np.ndarray, params: CfarParams | None = None) -> list[Contact]:
    p = params or CfarParams()
    img = scene.intensity
    mu = _train_mean(img, p)

    n_train = p.train_px**2 - p.guard_px**2
    alpha = n_train * (p.pfa ** (-1.0 / n_train) - 1.0)

    exceed = (img > alpha * mu) & ocean
    blobs = label(exceed, connectivity=2)

    contacts: list[Contact] = []
    for r in regionprops(blobs, intensity_image=img):
        if not (p.min_area_px <= r.area <= p.max_area_px):
            continue
        rr, cc = r.centroid_weighted
        lat, lon = scene.pixel_to_latlon(rr, cc)
        length_m = r.axis_major_length * scene.pixel_spacing_m
        width_m = max(r.axis_minor_length * scene.pixel_spacing_m,
                      scene.pixel_spacing_m)  # never report 0 width
        clutter = float(mu[int(rr), int(cc)])
        peak = float(r.intensity_max)
        contacts.append(Contact(
            row=float(rr), col=float(cc), lat=float(lat), lon=float(lon),
            area_px=int(r.area), peak_sigma0=peak,
            mean_sigma0=float(r.intensity_mean), clutter_mu=clutter,
            contrast_db=float(10.0 * np.log10(max(peak, 1e-12) / clutter)),
            length_m=float(length_m), width_m=float(width_m),
            orientation_deg=float(np.degrees(r.orientation) % 180.0),
            bbox=tuple(int(v) for v in r.bbox)))
    return contacts
