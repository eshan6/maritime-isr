"""Detector v2 — learned discriminator over CFAR candidates (roadmap 1.2).

Job: kill the false positives CFAR is honest enough to admit — sea
clutter spikes, azimuth ambiguities (the ghost detections offset from
strong reflectors along-track), fixed infrastructure. Two layers:

1. **Ghost pass (deterministic, scene-level).** Sentinel-1 IW azimuth
   ambiguities appear as dim replicas of a bright target displaced along
   the azimuth axis at a near-fixed ground offset (~4–6 km, PRF-dependent).
   For every candidate we look for a much brighter candidate at that
   offset; the intensity ratio becomes both a hard flag and a model
   feature. This is physics, not learning — no training set required to
   exploit it, so it can never regress with a bad retrain.

2. **Learned discriminator (chip features → gradient-boosted trees).**
   Compact, inspectable features per candidate chip: contrast, blob
   geometry, local clutter texture, ghost ratio, land distance. In the
   sandbox it trains on labeled synthetic candidates; on the deploy host
   the identical interface retrains on xView3-SAR + SSDD chips and GFW
   weak labels over the AOI (roadmap 1.2), and `cnn.py` provides the
   drop-in torch CNN for when chip volume justifies it. The pipeline
   only ever calls `score(candidates)` — swapping the model never
   touches the pipeline (connector-shaped everything, principle 5).

Model artifacts are versioned to PIPELINE_VERSION; the eval harness
gates every swap (principle 3).
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import PIPELINE_VERSION
from .cfar import Contact

CHIP_HALF = 32  # 64x64 px chip ≈ 640 m at S1 GRD spacing

GHOST_OFFSET_M = (3500.0, 6500.0)  # azimuth-ambiguity ground-offset search band
GHOST_RATIO_DB = 8.0               # candidate this much dimmer than its parent → ghost

FEATURE_NAMES = [
    "contrast_db", "area_px", "elongation", "length_m",
    "peak_over_chip_med", "chip_cv", "ghost_parent_ratio_db", "n_bright_neighbors",
]


def extract_chip(scene, c: Contact, half: int = CHIP_HALF) -> np.ndarray:
    r, col = int(round(c.row)), int(round(c.col))
    r0, r1 = max(0, r - half), min(scene.shape[0], r + half)
    c0, c1 = max(0, col - half), min(scene.shape[1], col + half)
    return scene.intensity[r0:r1, c0:c1]


def ghost_parent_ratio_db(scene, c: Contact, all_contacts: list[Contact]) -> float:
    """dB ratio of the brightest plausible ambiguity parent to this
    candidate (0 if none found in the offset band). High → this is a ghost."""
    lo = GHOST_OFFSET_M[0] / scene.pixel_spacing_m
    hi = GHOST_OFFSET_M[1] / scene.pixel_spacing_m
    best = 0.0
    for other in all_contacts:
        if other is c:
            continue
        d_az = abs(other.row - c.row)          # azimuth axis = rows
        d_rg = abs(other.col - c.col)
        if lo <= d_az <= hi and d_rg <= 12 and other.peak_sigma0 > c.peak_sigma0:
            ratio = 10.0 * np.log10(other.peak_sigma0 / max(c.peak_sigma0, 1e-12))
            best = max(best, float(ratio))
    return best


def features(scene, c: Contact, all_contacts: list[Contact]) -> np.ndarray:
    chip = extract_chip(scene, c)
    med = float(np.median(chip)) or 1e-12
    elong = c.length_m / max(c.width_m, 1e-6)
    n_bright = sum(1 for o in all_contacts
                   if o is not c
                   and abs(o.row - c.row) < 50 and abs(o.col - c.col) < 50)
    return np.array([
        c.contrast_db,
        float(c.area_px),
        float(elong),
        c.length_m,
        float(c.peak_sigma0 / med),
        float(np.std(chip) / (np.mean(chip) + 1e-12)),
        ghost_parent_ratio_db(scene, c, all_contacts),
        float(n_bright),
    ], dtype=np.float64)


@dataclass
class Discriminator:
    model: object
    version: str = PIPELINE_VERSION

    def score(self, scene, contacts: list[Contact]) -> np.ndarray:
        """P(real vessel) per contact. The deterministic ghost pass is a
        hard override on top of the model — physics outranks the model."""
        if not contacts:
            return np.zeros(0)
        X = np.stack([features(scene, c, contacts) for c in contacts])
        proba = self.model.predict_proba(X)[:, 1]
        ghost_col = FEATURE_NAMES.index("ghost_parent_ratio_db")
        proba = np.where(X[:, ghost_col] >= GHOST_RATIO_DB,
                         np.minimum(proba, 0.05), proba)
        return proba

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(pickle.dumps(
            {"model": self.model, "version": self.version,
             "features": FEATURE_NAMES}))

    @classmethod
    def load(cls, path: str | Path) -> "Discriminator":
        d = pickle.loads(Path(path).read_bytes())
        return cls(model=d["model"], version=d["version"])


def train(X: np.ndarray, y: np.ndarray) -> Discriminator:
    from sklearn.ensemble import GradientBoostingClassifier
    m = GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                   learning_rate=0.08, random_state=17)
    m.fit(X, y)
    return Discriminator(model=m)
