"""Phase 1 pipeline: new scene lands → contacts published to the
detection store, no human touch (roadmap 1.4 exit criterion #3).

Flow per scene:
    CALIBRATED sigma0 → ocean mask → CFAR (v1) → discriminator (v2)
    → Contact rows conformed into the Phase 0 DETECTION schema
    → conformed.write() (parquet + catalog registration, one call)
    → scene status → PUBLISHED, scene→publish latency recorded.

Design points inherited from Phase 0, deliberately:
- Output lands in the **same DETECTION schema GFW's detections already
  occupy** — our contacts and ground truth are join-compatible from the
  first scene, which is what makes the eval harness and the Phase 3
  GFW cross-validation a query instead of a project.
- Both the raw CFAR score (contrast_db) and the discriminator probability
  are preserved; `score` carries P(vessel) and the threshold applied at
  publish time is a *recorded parameter*, so Phase 3 can re-cut the
  operating point without re-detection.
- Provenance: source `maritime_isr_sar_cfar_v2`, source_ref = scene product id,
  acquired_at = scene sensing time. Every contact answers where/when/which
  version — non-negotiable (principle 2).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import numpy as np
import pyarrow as pa

from .. import h3util as tiling
from ..config import AOI_V1, PIPELINE_VERSION
from ..provenance import now_iso, stamp
from ..schemas import DETECTION
from ..storage import catalog as cat, conformed
from . import cfar, landmask
from .classifier import Discriminator
from .scene import Sigma0Scene

SOURCE = "maritime_isr_sar_cfar_v2"
PUBLISH_THRESHOLD = 0.5   # recorded per run; tunable without re-detection


def detect_scene(scene: Sigma0Scene, disc: Discriminator | None,
                 params: cfar.CfarParams | None = None,
                 coast_buffer_m: float = landmask.DEFAULT_COAST_BUFFER_M):
    """Pure detection: scene → (kept contacts, scores, diagnostics)."""
    ocean = landmask.ocean_mask(scene, coast_buffer_m)
    contacts = cfar.detect(scene, ocean, params)
    if disc is not None:
        scores = disc.score(scene, contacts)
    else:  # v1-only fallback: monotone map of contrast into (0,1)
        scores = np.array([1.0 - 10.0 ** (-c.contrast_db / 10.0) for c in contacts])
    keep = [(c, float(s)) for c, s in zip(contacts, scores) if s >= PUBLISH_THRESHOLD]
    return keep, contacts, ocean


def conform(scene: Sigma0Scene, kept: list) -> pa.Table:
    acq = (scene.acquired_at or datetime.now(timezone.utc))
    prov = stamp(SOURCE, scene.scene_id, acq.isoformat())
    rows = {name: [] for name in DETECTION.names}
    for c, s in kept:
        rows["detection_id"].append(f"{scene.scene_id}:{uuid.uuid4().hex[:10]}")
        rows["lat"].append(c.lat); rows["lon"].append(c.lon)
        rows["ts"].append(acq)
        rows["length_m"].append(c.length_m)
        rows["score"].append(s)
        rows["scene_id"].append(scene.scene_id)
        rows["matched_mmsi"].append(None)          # Phase 3 fills this
        rows["h3_cell"].append(tiling.cell(c.lat, c.lon))
        rows["source"].append(prov.source)
        rows["source_ref"].append(prov.source_ref)
        rows["acquired_at"].append(acq)
        rows["ingested_at"].append(datetime.now(timezone.utc))
        rows["pipeline_version"].append(PIPELINE_VERSION)
    return pa.table(rows, schema=DETECTION)


def process_scene(scene: Sigma0Scene, disc: Discriminator | None,
                  params: cfar.CfarParams | None = None) -> dict:
    """Full automatic path for one scene. Returns a run report."""
    t0 = time.monotonic()
    kept, all_contacts, ocean = detect_scene(scene, disc, params)
    tbl = conform(scene, kept)
    day = (scene.acquired_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    out = conformed.write(tbl, "detections", source=SOURCE,
                          aoi=AOI_V1.name, partition_day=day)
    elapsed = time.monotonic() - t0
    with cat.connect() as con:
        cat.upsert_scene(con, product_id=scene.scene_id, title=scene.scene_id,
                         sensing_time=(scene.acquired_at or datetime.now(timezone.utc)).isoformat(),
                         orbit_direction=None, relative_orbit=None, footprint_wkt=None)
        cat.set_scene_status(con, scene.scene_id, "PUBLISHED")
    return {
        "scene_id": scene.scene_id,
        "n_candidates": len(all_contacts),
        "n_published": len(kept),
        "ocean_area_km2": scene.ocean_area_km2(ocean),
        "publish_threshold": PUBLISH_THRESHOLD,
        "latency_s": elapsed,
        "parquet": str(out),
        "published_at": now_iso(),
    }
