"""Unit 0.5 — snapshot exporter for the week-4 inspection dashboard.

Dumps a small GeoJSON-ish JSON blob from DuckDB (recent AIS tracks + S1 scene
footprints over the AOI) that inspect/v0/index.html renders. Deliberately
minimal — this is the throwaway inspection view, zero polish budget (rule 6).
"""
from __future__ import annotations

import json
from datetime import timedelta

from ..config import cfg
from ..db import connect, ensure_scene_catalog, table_exists
from ..schemas import utcnow


def export(hours: int = 24, out_path: str | None = None) -> str:
    con = connect(read_only=False)
    ensure_scene_catalog(con)
    since = utcnow() - timedelta(hours=hours)

    tracks: dict[str, list] = {}
    try:
        rows = con.execute(
            """
            SELECT mmsi, lon, lat, epoch(timestamp) AS t
            FROM ais
            WHERE timestamp >= ?
            ORDER BY mmsi, timestamp
            """,
            [since],
        ).fetchall()
        for mmsi, lon, lat, t in rows:
            tracks.setdefault(str(mmsi), []).append([lon, lat, t])
    except Exception as e:  # noqa: BLE001
        print(f"[inspect] no AIS yet ({e})")

    scenes = []
    if table_exists(con, "scene_catalog"):
        for scene_id, wkt, status, acq in con.execute(
            "SELECT scene_id, footprint_wkt, status, epoch(acquired_at) FROM scene_catalog"
        ).fetchall():
            scenes.append({"scene_id": scene_id, "wkt": wkt, "status": status, "t": acq})

    a = cfg.aoi
    blob = {
        "aoi": {"lat_min": a.lat_min, "lat_max": a.lat_max,
                "lon_min": a.lon_min, "lon_max": a.lon_max, "name": a.name},
        "generated_at": utcnow().isoformat(),
        "window_hours": hours,
        "tracks": tracks,
        "scenes": scenes,
        "counts": {"vessels": len(tracks),
                   "positions": sum(len(v) for v in tracks.values()),
                   "scenes": len(scenes)},
    }
    out = out_path or str(cfg.data_root / "inspect_v0_snapshot.json")
    with open(out, "w") as fh:
        json.dump(blob, fh)
    print(f"[inspect] wrote {out}: {blob['counts']}")
    return out


if __name__ == "__main__":
    export()
