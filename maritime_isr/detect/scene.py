"""Sigma0 scene container — the single shape every Phase 1 stage consumes.

A calibrated Sentinel-1 GRD scene (post-SNAP sigma0 GeoTIFF on the deploy
host, or a synthetic .npz in the eval/test path) is loaded into a
`Sigma0Scene`: linear-power intensity array + an affine lat/lon
geotransform + pixel spacing. The detector never sees file formats.

Deploy host: `load_geotiff` (rasterio, guarded import — same posture as
the SNAP toolchain in Phase 0: heavy dependency stays on the host that
has it, the code path is version-controlled here).
Everywhere:  `load_npz` for synthetic scenes and cached chips.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass
class Sigma0Scene:
    scene_id: str
    intensity: np.ndarray          # 2D float32, linear power sigma0 (VV)
    lat0: float                    # lat of row 0 (north edge)
    lon0: float                    # lon of col 0 (west edge)
    dlat: float                    # deg per row (negative: rows go south)
    dlon: float                    # deg per col
    pixel_spacing_m: float         # ground spacing, metres (S1 IW GRD ~10 m)
    acquired_at: datetime | None = None
    azimuth_axis: int = 0          # rows ≈ along-track for IW GRD; ghosts offset on this axis

    @property
    def shape(self) -> tuple[int, int]:
        return self.intensity.shape

    def pixel_to_latlon(self, row: float, col: float) -> tuple[float, float]:
        return self.lat0 + row * self.dlat, self.lon0 + col * self.dlon

    def latlon_grid(self, step: int = 1) -> tuple[np.ndarray, np.ndarray]:
        rows = np.arange(0, self.shape[0], step)
        cols = np.arange(0, self.shape[1], step)
        lats = self.lat0 + rows * self.dlat
        lons = self.lon0 + cols * self.dlon
        return np.meshgrid(lats, lons, indexing="ij")

    def ocean_area_km2(self, ocean_mask: np.ndarray) -> float:
        return float(ocean_mask.sum()) * (self.pixel_spacing_m / 1000.0) ** 2


def load_npz(path: str | Path) -> Sigma0Scene:
    z = np.load(path, allow_pickle=False)
    acq = None
    if "acquired_at" in z:
        acq = datetime.fromisoformat(str(z["acquired_at"]))
    return Sigma0Scene(
        scene_id=str(z["scene_id"]), intensity=z["intensity"].astype(np.float32),
        lat0=float(z["lat0"]), lon0=float(z["lon0"]),
        dlat=float(z["dlat"]), dlon=float(z["dlon"]),
        pixel_spacing_m=float(z["pixel_spacing_m"]), acquired_at=acq)


def load_geotiff(path: str | Path, scene_id: str,
                 acquired_at: datetime | None = None) -> Sigma0Scene:
    """Deploy-host loader for SNAP sigma0 output. rasterio is not a
    sandbox dependency; import stays inside the function."""
    import rasterio  # noqa: deploy-host dependency
    with rasterio.open(path) as ds:
        band = ds.read(1).astype(np.float32)
        t = ds.transform
        # approximate ground spacing from degree spacing at scene centre lat
        centre_lat = t.f + t.e * ds.height / 2
        m_per_deg = 111_320.0 * np.cos(np.deg2rad(centre_lat))
        return Sigma0Scene(scene_id=scene_id, intensity=band,
                           lat0=t.f, lon0=t.c, dlat=t.e, dlon=t.a,
                           pixel_spacing_m=abs(t.a) * m_per_deg,
                           acquired_at=acquired_at)
