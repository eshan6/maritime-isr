"""Land/coastline masking (roadmap 1.1: 'this is where most naive
pipelines die — a bad land mask floods you with false contacts from
breakwaters, islets, and aquaculture').

Source: the GLOBE-derived 1 km global land mask (`global_land_mask`),
evaluated on the scene's own pixel grid, then **dilated seaward** by a
configurable buffer. The buffer is the operationally load-bearing part:
the 1 km mask resolution plus geolocation error plus harbour furniture
(breakwaters, moles, aquaculture rafts) all live inside ~500–1000 m of
the coastline, and every one of them is a CFAR contact if left exposed.

The dilation cost is honest and stated: real vessels moored inside the
buffer are invisible to Phase 1. That is a *declared capability boundary*
(same posture as the 15–25 m detection floor), not a silent gap — port
traffic is AIS-dense, so Phase 2/3 carry those vessels; SAR earns its
keep offshore where the mask costs nothing.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation

from .scene import Sigma0Scene

DEFAULT_COAST_BUFFER_M = 750.0
_MASK_EVAL_STEP = 4  # evaluate the 1 km mask every Nth pixel, then upsample


def ocean_mask(scene: Sigma0Scene,
               coast_buffer_m: float = DEFAULT_COAST_BUFFER_M) -> np.ndarray:
    """Boolean array, True = ocean pixels eligible for detection."""
    step = _MASK_EVAL_STEP
    lat_g, lon_g = scene.latlon_grid(step=step)
    from global_land_mask import globe
    land_coarse = globe.is_land(lat_g, lon_g)

    # upsample coarse land mask back to full pixel grid
    land = np.repeat(np.repeat(land_coarse, step, axis=0), step, axis=1)
    land = land[: scene.shape[0], : scene.shape[1]]
    if land.shape != scene.shape:  # pad any residual edge
        pad = np.ones(scene.shape, dtype=bool)
        pad[: land.shape[0], : land.shape[1]] = land
        land = pad

    buffer_px = max(1, int(round(coast_buffer_m / scene.pixel_spacing_m)))
    land_dilated = binary_dilation(land, iterations=buffer_px)
    return ~land_dilated
