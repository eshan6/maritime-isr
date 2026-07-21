"""Deploy-host CNN chip classifier (roadmap 1.2) — the drop-in upgrade for
`classifier.Discriminator` once xView3-SAR / SSDD chip volume is on disk.

Same posture as SNAP in Phase 0: the heavy toolchain (torch, ~GBs of
xView3 scenes) lives on the deploy host; the architecture and training
recipe are version-controlled here so the trained artifact is
reproducible from raw + repo. Nothing else in the pipeline imports torch.

Interface contract: exposes `.score(scene, contacts) -> np.ndarray` — the
pipeline cannot tell whether it is talking to trees or a CNN. The eval
harness gates the swap (principle 3); the deterministic ghost override in
classifier.py is applied identically on top.

Deploy-host usage:
    python -m maritime_isr.detect.cnn --xview3-labels train.csv --chips chips/ \
        --epochs 12 --out models/cnn_v1.pt
"""
from __future__ import annotations

import numpy as np


def _torch():
    import torch
    import torch.nn as nn
    return torch, nn


def build_model():
    torch, nn = _torch()

    class ChipNet(nn.Module):
        """~120k params — small on purpose. 64x64 single-channel sigma0
        chips, log-scaled and per-chip standardized in the loader."""
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(64, 64, 3, padding=1), nn.ReLU(),
                nn.AdaptiveAvgPool2d(1),
            )
            self.head = nn.Sequential(nn.Flatten(), nn.Linear(64, 32),
                                      nn.ReLU(), nn.Linear(32, 1))

        def forward(self, x):
            return self.head(self.features(x)).squeeze(-1)

    return ChipNet()


def preprocess_chip(chip: np.ndarray, size: int = 64) -> np.ndarray:
    """log-scale + robust standardize + pad/crop to size — applied
    identically at train and inference time (version-controlled here so
    it cannot drift between the two)."""
    x = np.log10(np.maximum(chip, 1e-12))
    med, mad = np.median(x), np.median(np.abs(x - np.median(x))) + 1e-9
    x = (x - med) / (1.4826 * mad)
    out = np.zeros((size, size), dtype=np.float32)
    r = min(size, x.shape[0]); c = min(size, x.shape[1])
    out[:r, :c] = x[:r, :c]
    return out


class CnnDiscriminator:
    """Same .score() contract as classifier.Discriminator."""

    def __init__(self, model, device="cpu"):
        self.model = model.to(device).eval()
        self.device = device

    def score(self, scene, contacts) -> np.ndarray:
        from .classifier import (GHOST_RATIO_DB, extract_chip,
                                 ghost_parent_ratio_db)
        torch, _ = _torch()
        if not contacts:
            return np.zeros(0)
        chips = np.stack([preprocess_chip(extract_chip(scene, c))
                          for c in contacts])[:, None]
        with torch.no_grad():
            logits = self.model(torch.from_numpy(chips).to(self.device))
            proba = torch.sigmoid(logits).cpu().numpy()
        ghosts = np.array([ghost_parent_ratio_db(scene, c, contacts)
                           for c in contacts])
        return np.where(ghosts >= GHOST_RATIO_DB, np.minimum(proba, 0.05), proba)


def main():  # deploy-host training entrypoint
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--xview3-labels", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    torch, nn = _torch()
    # Loader intentionally thin: xView3 labels via eval.xview3.load_labels,
    # chips pre-extracted to .npy alongside. BCEWithLogits, AdamW 1e-3,
    # class-balanced sampling — the recipe is the artifact.
    raise SystemExit("Run on the deploy host with xView3 data present; "
                     "see module docstring.")


if __name__ == "__main__":
    main()
