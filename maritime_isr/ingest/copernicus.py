"""Unit 0.1 — Copernicus Sentinel-1 GRD connector.

Query the Copernicus Data Space STAC/OData catalog by AOI + time window,
maintain a scene catalog (footprint, orbit, timestamp, status), and land raw
IW-mode VV/VH GRD products to R2 with a resumable, idempotent downloader.

Idempotency: raw products are content-addressed by scene id (store.raw_scene_key);
a scene already present in R2 (or already RAW in the catalog) is skipped, so
re-running a backfill downloads nothing new. Catalog upserts are keyed on scene_id.

Auth: CDSE uses a Keycloak token exchange (username/password -> access token).
Downloads stream to a temp file then upload to R2 (never hold a scene in memory).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import requests

from ..config import cfg
from ..db import connect, ensure_scene_catalog
from ..schemas import Provenance, SceneStatus, git_sha, utcnow
from ..store import r2_key_exists, r2_put_file, raw_scene_key

CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
ZIPPER = "https://zipper.dataspace.copernicus.eu/odata/v1/Products"
SOURCE_ID = "copernicus-s1"


def _token() -> str:
    user, pw = os.getenv("CDSE_USERNAME"), os.getenv("CDSE_PASSWORD")
    if not user or not pw:
        raise RuntimeError("CDSE_USERNAME / CDSE_PASSWORD unset — cannot authenticate")
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": "cdse-public",
            "username": user,
            "password": pw,
            "grant_type": "password",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _odata_filter(days: int) -> str:
    a = cfg.aoi
    start = (utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end = utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    aoi_wkt = a.wkt
    # IW GRD, either polarization set; intersecting the AOI polygon.
    return (
        "Collection/Name eq 'SENTINEL-1' "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}') "
        f"and ContentDate/Start gt {start} and ContentDate/Start lt {end} "
        "and contains(Name,'GRD') and contains(Name,'IW')"
    )


def query_catalog(days: int) -> list[dict]:
    """Return raw OData product dicts for the AOI+window, paging through results."""
    products: list[dict] = []
    params = {
        "$filter": _odata_filter(days),
        "$orderby": "ContentDate/Start desc",
        "$top": "100",
        "$expand": "Attributes",
    }
    url = CATALOG_URL
    while True:
        r = requests.get(url, params=params if url == CATALOG_URL else None, timeout=60)
        r.raise_for_status()
        payload = r.json()
        products.extend(payload.get("value", []))
        nxt = payload.get("@odata.nextLink")
        if not nxt:
            break
        url = nxt
    return products


def _attr(prod: dict, name: str):
    for a in prod.get("Attributes", []):
        if a.get("Name") == name:
            return a.get("Value")
    return None


def _upsert_catalog(con, prod: dict) -> str:
    scene_id = prod["Name"].replace(".SAFE", "")
    prov = Provenance(
        source_id=SOURCE_ID,
        source_ref=prod["Id"],
        acquired_at=datetime.fromisoformat(prod["ContentDate"]["Start"].replace("Z", "+00:00")),
    )
    footprint = prod.get("Footprint") or prod.get("GeoFootprint")
    fp_wkt = ""
    if isinstance(footprint, str) and ";" in footprint:
        fp_wkt = footprint.split(";", 1)[1].strip("'")
    elif isinstance(footprint, dict):
        fp_wkt = str(footprint)
    con.execute(
        """
        INSERT INTO scene_catalog (
            scene_id, footprint_wkt, orbit_direction, relative_orbit, acquired_at,
            mode, polarizations, status, status_detail, raw_uri, calibrated_uri,
            source_id, source_ref, provenance_acquired_at, ingested_at,
            pipeline_version, confidence
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (scene_id) DO NOTHING
        """,
        [
            scene_id, fp_wkt, _attr(prod, "orbitDirection"),
            _attr(prod, "relativeOrbitNumber"),
            prov.acquired_at, "IW", _attr(prod, "polarisationChannels") or "VV+VH",
            SceneStatus.CATALOGED.value, None, None, None,
            SOURCE_ID, prod["Id"], prov.acquired_at, prov.ingested_at,
            prov.pipeline_version, None,
        ],
    )
    return scene_id


def _download_scene(con, scene_id: str, product_id: str, token: str) -> None:
    key = raw_scene_key(scene_id)
    if r2_key_exists(key):
        con.execute(
            "UPDATE scene_catalog SET status=?, raw_uri=? WHERE scene_id=?",
            [SceneStatus.RAW.value, f"s3://{os.environ['R2_BUCKET']}/{key}", scene_id],
        )
        return
    url = f"{ZIPPER}({product_id})/$value"
    headers = {"Authorization": f"Bearer {token}"}
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=True) as tmp:
        with requests.get(url, headers=headers, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)
        tmp.flush()
        uri = r2_put_file(tmp.name, key)
    con.execute(
        "UPDATE scene_catalog SET status=?, raw_uri=? WHERE scene_id=?",
        [SceneStatus.RAW.value, uri, scene_id],
    )


def run(days: int = 90, catalog_only: bool = False) -> int:
    con = connect()
    ensure_scene_catalog(con)
    print(f"[copernicus] querying AOI={cfg.aoi.name} window={days}d ...")
    products = query_catalog(days)
    print(f"[copernicus] {len(products)} products match")
    for prod in products:
        _upsert_catalog(con, prod)
    if catalog_only:
        from ..db import scene_count
        print(f"[copernicus] catalog now holds {scene_count(con)} scenes (catalog-only)")
        return 0
    token = _token()
    to_fetch = con.execute(
        "SELECT scene_id, source_ref FROM scene_catalog WHERE status = ?",
        [SceneStatus.CATALOGED.value],
    ).fetchall()
    print(f"[copernicus] downloading {len(to_fetch)} new scenes ...")
    for i, (scene_id, product_id) in enumerate(to_fetch, 1):
        try:
            _download_scene(con, scene_id, product_id, token)
            print(f"  [{i}/{len(to_fetch)}] {scene_id} -> RAW")
        except Exception as e:  # noqa: BLE001
            con.execute(
                "UPDATE scene_catalog SET status=?, status_detail=? WHERE scene_id=?",
                [SceneStatus.FAILED.value, str(e)[:500], scene_id],
            )
            print(f"  [{i}/{len(to_fetch)}] {scene_id} FAILED: {e}")
    from ..db import scene_count
    print(f"[copernicus] done. catalog={scene_count(con)} raw={scene_count(con, 'raw')}")
    return 0
