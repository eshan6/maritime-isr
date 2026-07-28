"""Unit 0.2 — Sentinel-1 GRD preprocessing chain via pyroSAR + SNAP gpt.

PARKED: awaiting deploy host. Requires ESA SNAP installed, which laptop mode
excludes, and operates on downloaded SAR imagery, which the 1 GB disk budget
excludes. Kept intact for the deploy host. See DATA_SOURCES.md.

Chain (spec exit test): orbit file -> thermal noise removal -> calibration to
sigma-nought -> terrain correction (geocoding) -> cloud-optimized GeoTIFF, with
scene_catalog status transitioning raw -> calibrated.

Design choices (verified against pyroSAR docs, not memory):
  * pyroSAR drives SNAP via `gpt` and workflow XMLs (NOT the snappy Python
    bridge) — geocode() parametrizes a workflow for the scene and calls gpt.
  * Our AOI is mostly open ocean, so terrain correction is geocoding-only
    (Range-Doppler to WGS84) WITHOUT radiometric terrain flattening. This is
    correct for water and avoids SNAP's infinite DEM-tile-download failure mode
    over areas that don't need a DEM.
  * Output sigma-nought in dB (scaling='dB'), the input CFAR expects in 1.1.
  * Memory-capped by the gpt.vmoptions written in install_snap.sh; here we also
    keep per-scene temp dirs and clean them so the 24 GB VM doesn't fill disk.

pyroSAR's geocode() arg names shifted across versions (spacing vs tr, refarea).
We introspect the signature and pass the right ones so this survives a pyroSAR
upgrade instead of silently mis-calling.
"""
from __future__ import annotations

import inspect
import os
import shutil
import tempfile
from pathlib import Path

from ..config import cfg
from ..db import connect, ensure_scene_catalog
from ..schemas import SceneStatus, git_sha, utcnow
from ..store import raw_scene_key

# Target ground resolution (m). Sentinel-1 IW GRDH native ~10 m; 10 keeps the
# detection floor low for small vessels (1.1 notes ~15-25 m detectable).
TARGET_SPACING_M = 10.0
SOURCE_ID = "copernicus-s1"


def _geocode_kwargs(geocode_fn, infile: str, outdir: str, tmpdir: str) -> dict:
    """Build version-correct kwargs for pyroSAR.snap.geocode by introspection."""
    sig = inspect.signature(geocode_fn)
    params = sig.parameters
    kw: dict = {"infile": infile, "outdir": outdir}
    # resolution: newer pyroSAR uses 'spacing', older used 'tr'
    if "spacing" in params:
        kw["spacing"] = TARGET_SPACING_M
    elif "tr" in params:
        kw["tr"] = TARGET_SPACING_M
    # sigma-nought
    if "refarea" in params:
        kw["refarea"] = "sigma0"
    # dB scaling for CFAR input
    if "scaling" in params:
        kw["scaling"] = "dB"
    # geocoding-only terrain correction (no radiometric flattening over ocean)
    if "terrainFlattening" in params:
        kw["terrainFlattening"] = False
    # thermal + border noise (ocean: ESA border-noise method is fine)
    if "removeS1ThermalNoise" in params:
        kw["removeS1ThermalNoise"] = True
    if "removeS1BorderNoise" in params:
        kw["removeS1BorderNoise"] = True
    # output CRS WGS84
    if "t_srs" in params:
        kw["t_srs"] = 4326
    # keep SNAP's own tmp under our controlled dir
    if "tmpdir" in params:
        kw["tmpdir"] = tmpdir
    # GeoTIFF output where supported (else BEAM-DIMAP -> we convert to COG)
    if "export_extra" in params:
        kw["export_extra"] = None
    return kw


def _fetch_raw_to_local(con, scene_id: str, raw_uri: str | None, workdir: Path) -> Path:
    """Pull the raw .zip from R2 (or use a local path) into workdir for gpt."""
    local_zip = workdir / f"{scene_id}.zip"
    if raw_uri and raw_uri.startswith("s3://"):
        from ..store import _r2_client  # lazy
        client = _r2_client()
        bucket = os.environ["R2_BUCKET"]
        key = raw_scene_key(scene_id)
        client.download_file(bucket, key, str(local_zip))
    elif raw_uri and Path(raw_uri).exists():
        shutil.copy2(raw_uri, local_zip)
    else:
        raise FileNotFoundError(f"raw product for {scene_id} not found (uri={raw_uri})")
    return local_zip


def _to_cog(src_tif: Path, dst_cog: Path) -> None:
    """Rewrite a GeoTIFF as a cloud-optimized GeoTIFF (tiled + overviews)."""
    import rasterio
    from rasterio.shutil import copy as rio_copy

    with rasterio.open(src_tif) as src:
        profile = src.profile.copy()
        profile.update(driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate")
        rio_copy(src, dst_cog, copy_src_overviews=True, **{
            "TILED": "YES", "COPY_SRC_OVERVIEWS": "YES", "COMPRESS": "DEFLATE",
            "BLOCKXSIZE": "512", "BLOCKYSIZE": "512",
        })


def preprocess_scene(con, scene_id: str, raw_uri: str | None) -> str:
    """Run the full chain on one scene. Returns the calibrated COG URI."""
    from pyroSAR.snap import geocode  # lazy: only needed on the SNAP box

    workdir = Path(tempfile.mkdtemp(prefix=f"s1_{scene_id}_"))
    out_dir = cfg.data_root / "calibrated"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw_zip = _fetch_raw_to_local(con, scene_id, raw_uri, workdir)
        snap_tmp = workdir / "snap_tmp"
        snap_tmp.mkdir(exist_ok=True)

        kw = _geocode_kwargs(geocode, str(raw_zip), str(out_dir), str(snap_tmp))
        print(f"[s1_preprocess] {scene_id}: geocode({', '.join(k for k in kw if k not in ('infile','outdir','tmpdir'))})")
        geocode(**kw)

        # pyroSAR writes <name>_<pol>_<...>.tif per polarization into out_dir.
        produced = sorted(out_dir.glob(f"*{scene_id[-15:]}*.tif")) or \
                   sorted(out_dir.glob("*.tif"))
        if not produced:
            raise RuntimeError("geocode produced no GeoTIFF (check gpt logs / SNAP install)")

        # Convert the primary product to COG in place.
        primary = produced[-1]
        cog_path = out_dir / f"{scene_id}_sigma0_db_cog.tif"
        try:
            _to_cog(primary, cog_path)
            final = cog_path
        except Exception as e:  # noqa: BLE001
            print(f"[s1_preprocess] COG conversion skipped ({e}); keeping plain GeoTIFF")
            final = primary

        calibrated_uri = str(final)
        con.execute(
            "UPDATE scene_catalog SET status=?, calibrated_uri=?, pipeline_version=? WHERE scene_id=?",
            [SceneStatus.CALIBRATED.value, calibrated_uri, git_sha(), scene_id],
        )
        print(f"[s1_preprocess] {scene_id} -> CALIBRATED: {calibrated_uri}")
        return calibrated_uri
    except Exception as e:  # noqa: BLE001
        con.execute(
            "UPDATE scene_catalog SET status=?, status_detail=? WHERE scene_id=?",
            [SceneStatus.FAILED.value, str(e)[:500], scene_id],
        )
        print(f"[s1_preprocess] {scene_id} FAILED: {e}")
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run(limit: int | None = None) -> int:
    """Preprocess all RAW scenes (status=raw) into calibrated sigma-nought COGs."""
    con = connect()
    ensure_scene_catalog(con)
    rows = con.execute(
        "SELECT scene_id, raw_uri FROM scene_catalog WHERE status = ? ORDER BY acquired_at",
        [SceneStatus.RAW.value],
    ).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"[s1_preprocess] {len(rows)} scene(s) to process")
    ok = 0
    for scene_id, raw_uri in rows:
        try:
            preprocess_scene(con, scene_id, raw_uri)
            ok += 1
        except Exception:  # noqa: BLE001
            continue  # status already set to FAILED; keep going
    from ..db import scene_count
    print(f"[s1_preprocess] done. calibrated={scene_count(con, 'calibrated')} "
          f"failed={scene_count(con, 'failed')} (this run ok={ok})")
    return 0
