"""Unit 0.4 — Global Fishing Watch connector.

Pulls GFW's published SAR detections over our AOI (the gifted ground truth that
feeds every evaluation harness after Phase 1) plus vessel presence. Detections
land in the canonical Detection schema with method='gfw' so Phase 1/3 can
cross-validate our own detections against them.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from ..config import cfg
from ..schemas import Detection, DetectionMethod, Provenance
from ..h3util import index_both
from ..writer_detections import write_detections

SOURCE_ID = "gfw"
API = "https://gateway.api.globalfishingwatch.org/v3"


def _headers() -> dict:
    tok = os.getenv("GFW_API_TOKEN")
    if not tok:
        raise RuntimeError("GFW_API_TOKEN unset")
    return {"Authorization": f"Bearer {tok}"}


def fetch_sar_detections(days: int = 90) -> list[dict]:
    """Query GFW SAR fixed-infrastructure / vessel detections in the AOI window.

    GFW's API surface evolves; this targets the 4wings/report style endpoint and
    degrades gracefully (returns [] with a clear message) if the dataset slug
    needs updating, so the unit doesn't hard-fail your pipeline.
    """
    a = cfg.aoi
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "datasets[0]": "public-global-sar-presence:latest",
        "start-date": start.strftime("%Y-%m-%d"),
        "end-date": end.strftime("%Y-%m-%d"),
        "geopolygon": a.wkt,
        "format": "json",
    }
    try:
        r = requests.get(f"{API}/4wings/report", headers=_headers(),
                         params=params, timeout=120)
        r.raise_for_status()
        return r.json().get("entries", [])
    except requests.HTTPError as e:
        print(f"[gfw] SAR endpoint returned {e}; check dataset slug/version. "
              "Landing nothing this run.")
        return []


def run(days: int = 90) -> int:
    raw = fetch_sar_detections(days)
    print(f"[gfw] {len(raw)} SAR detection records")
    rows = []
    for i, d in enumerate(raw):
        try:
            lat, lon = float(d["lat"]), float(d["lon"])
            ts = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00"))
            r7, r9 = index_both(lat, lon)
            det = Detection(
                detection_id=f"gfw-{ts:%Y%m%d}-{i}",
                scene_id="gfw-external",
                method=DetectionMethod.GFW,
                lat=lat, lon=lon,
                length_m=d.get("length_m"),
                h3_r7=r7, h3_r9=r9,
                acquired_at=ts,
                prov=Provenance(source_id=SOURCE_ID, source_ref=str(i), acquired_at=ts,
                                confidence=d.get("score")),
            )
            row = det.model_dump()
            prov = det.prov.stamp()
            row["method"] = det.method.value
            row.pop("prov")
            row.update(prov)
            rows.append(row)
        except Exception:  # noqa: BLE001
            continue
    if rows:
        write_detections(rows)
    print(f"[gfw] landed {len(rows)} detections (method=gfw)")
    return 0
