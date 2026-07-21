"""Sentinel-1 GRD connector (roadmap 0.1 #1) — Copernicus Data Space.

Split into three separable stages so each is independently retryable and
the scene catalog is the single source of truth for pipeline state:

  DISCOVER  — OData query by AOI+time -> upsert scenes as DISCOVERED
  DOWNLOAD  — fetch product zips -> immutable raw store -> DOWNLOADED
  CALIBRATE — ESA preprocessing chain (SNAP gpt subprocess, graph XML
              below) -> sigma-nought terrain-corrected GeoTIFF -> CALIBRATED

Network calls are isolated in `_http_get` so the whole module is testable
offline and the deployed environment only needs credentials + egress to
catalogue.dataspace.copernicus.eu.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..config import AOI
from ..storage import catalog as cat

ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


def build_query(aoi: AOI, t_start: str, t_end: str, top: int = 200) -> str:
    """OData query: IW-mode GRD intersecting the AOI in [t_start, t_end).
    Kept as a pure function — unit-tested without network."""
    filt = (
        "Collection/Name eq 'SENTINEL-1' "
        "and contains(Name,'GRDH') and contains(Name,'IW') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{aoi.wkt}') "
        f"and ContentDate/Start ge {t_start} "
        f"and ContentDate/Start lt {t_end}"
    )
    params = {"$filter": filt, "$top": str(top),
              "$orderby": "ContentDate/Start asc",
              "$expand": "Attributes"}
    return f"{ODATA_BASE}?{urllib.parse.urlencode(params)}"


def _http_get(url: str, token: str | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_odata_response(payload: dict) -> list[dict]:
    """Normalize an OData product listing into scene-catalog rows."""
    out = []
    for p in payload.get("value", []):
        attrs = {a.get("Name"): a.get("Value") for a in p.get("Attributes", [])}
        fp = p.get("Footprint", "") or p.get("GeoFootprint", "")
        if isinstance(fp, dict):  # GeoJSON form
            fp = json.dumps(fp)
        out.append({
            "product_id": p["Id"],
            "title": p.get("Name", ""),
            "sensing_time": p.get("ContentDate", {}).get("Start", ""),
            "orbit_direction": attrs.get("orbitDirection"),
            "relative_orbit": attrs.get("relativeOrbitNumber"),
            "footprint_wkt": fp if isinstance(fp, str) else None,
        })
    return out


def discover(aoi: AOI, t_start: str, t_end: str, *, fetch=_http_get,
             token: str | None = None) -> int:
    """Query the catalogue, upsert DISCOVERED scenes. Paginates via
    @odata.nextLink. Returns number of scenes discovered."""
    url = build_query(aoi, t_start, t_end)
    n = 0
    with cat.connect() as con:
        while url:
            payload = json.loads(fetch(url, token))
            for row in parse_odata_response(payload):
                cat.upsert_scene(con, **row)
                n += 1
            url = payload.get("@odata.nextLink")
    return n


def download_pending(*, fetch=_http_get, token: str | None = None,
                     limit: int = 10) -> int:
    """Fetch DISCOVERED scenes into the immutable raw store."""
    from ..storage import raw
    n = 0
    with cat.connect() as con:
        for scene in list(cat.scenes_by_status(con, "DISCOVERED"))[:limit]:
            url = f"{ODATA_BASE}({scene['product_id']})/$value"
            payload = fetch(url, token)
            path, sha = raw.land("sentinel1_grd", f"{scene['title']}.zip", payload,
                                 day=scene["sensing_time"][:10])
            cat.set_scene_status(con, scene["product_id"], "DOWNLOADED", str(path))
            cat.register_artifact(con, source="copernicus_dataspace", kind="raw",
                                  path=str(path), sha256=sha,
                                  t_start=scene["sensing_time"], t_end=scene["sensing_time"],
                                  aoi=None, pipeline_version=__import__("maritime_isr.config", fromlist=["PIPELINE_VERSION"]).PIPELINE_VERSION)
            n += 1
    return n


# ESA preprocessing chain per roadmap 0.1: orbit file -> thermal noise
# removal -> calibration to sigma0 -> terrain correction. Executed by SNAP
# `gpt` on the deployed host (SNAP is a multi-GB Java install; not run in
# the build sandbox). The graph is version-controlled here so preprocessing
# is reproducible from raw + this file — principle 1.
SNAP_GRAPH_XML = """<graph id=\"s1_grd_sigma0\">
  <version>1.0</version>
  <node id=\"Read\"><operator>Read</operator>
    <parameters><file>${input}</file></parameters></node>
  <node id=\"Orbit\"><operator>Apply-Orbit-File</operator>
    <sources><sourceProduct refid=\"Read\"/></sources>
    <parameters><orbitType>Sentinel Precise (Auto Download)</orbitType>
      <continueOnFail>true</continueOnFail></parameters></node>
  <node id=\"Noise\"><operator>ThermalNoiseRemoval</operator>
    <sources><sourceProduct refid=\"Orbit\"/></sources>
    <parameters><removeThermalNoise>true</removeThermalNoise></parameters></node>
  <node id=\"Cal\"><operator>Calibration</operator>
    <sources><sourceProduct refid=\"Noise\"/></sources>
    <parameters><outputSigmaBand>true</outputSigmaBand>
      <selectedPolarisations>VV,VH</selectedPolarisations></parameters></node>
  <node id=\"TC\"><operator>Terrain-Correction</operator>
    <sources><sourceProduct refid=\"Cal\"/></sources>
    <parameters><demName>SRTM 3Sec</demName>
      <pixelSpacingInMeter>10.0</pixelSpacingInMeter>
      <nodataValueAtSea>false</nodataValueAtSea></parameters></node>
  <node id=\"Write\"><operator>Write</operator>
    <sources><sourceProduct refid=\"TC\"/></sources>
    <parameters><file>${output}</file><formatName>GeoTIFF-BigTIFF</formatName></parameters></node>
</graph>
"""


def calibrate_command(input_zip: str, output_tif: str) -> list[str]:
    """The subprocess invocation the deploy host runs per DOWNLOADED scene."""
    return ["gpt", "graphs/s1_grd_sigma0.xml", f"-Pinput={input_zip}",
            f"-Poutput={output_tif}", "-q", "8"]
