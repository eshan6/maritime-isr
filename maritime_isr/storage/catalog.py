"""Lightweight metadata catalog. Two tables:

artifacts — every raw/conformed file: source, time window, sha, path, pipeline version.
scenes    — the Sentinel-1 scene catalog: product id, footprint WKT, orbit,
            sensing time, processing status (DISCOVERED → DOWNLOADED →
            CALIBRATED → PUBLISHED), so 'is the AOI current?' is one query.

SQLite is deliberate at prototype scale: single-file, transactional,
replaced by Postgres by the same interface when the engineering team
productionizes. Nothing outside this module speaks SQL.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterable

from ..config import CATALOG_DB
from ..provenance import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,              -- raw | conformed
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT,
  t_start TEXT, t_end TEXT,        -- data time window (ISO)
  aoi TEXT,
  pipeline_version TEXT NOT NULL,
  registered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_src_time ON artifacts(source, t_start, t_end);

CREATE TABLE IF NOT EXISTS scenes (
  product_id TEXT PRIMARY KEY,
  title TEXT,
  sensing_time TEXT NOT NULL,
  orbit_direction TEXT,
  relative_orbit INTEGER,
  footprint_wkt TEXT,
  status TEXT NOT NULL DEFAULT 'DISCOVERED',
  raw_path TEXT,
  status_updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scenes_time ON scenes(sensing_time);

CREATE TABLE IF NOT EXISTS registry_snapshots (
  id INTEGER PRIMARY KEY,
  registry TEXT NOT NULL,          -- ofac_sdn | un_consolidated | eu_consolidated | wpi_ports | ship_registry
  as_of TEXT NOT NULL,             -- the *as-of* date sanctions edges must carry
  sha256 TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  n_records INTEGER,
  n_added INTEGER, n_removed INTEGER, n_changed INTEGER,  -- diff vs previous snapshot
  registered_at TEXT NOT NULL,
  UNIQUE(registry, sha256)
);
"""


@contextmanager
def connect(db_path=None):
    con = sqlite3.connect(str(db_path or CATALOG_DB))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def register_artifact(con, *, source: str, kind: str, path: str, sha256: str | None,
                      t_start: str | None, t_end: str | None, aoi: str | None,
                      pipeline_version: str) -> None:
    con.execute(
        """INSERT OR IGNORE INTO artifacts
           (source,kind,path,sha256,t_start,t_end,aoi,pipeline_version,registered_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (source, kind, path, sha256, t_start, t_end, aoi, pipeline_version, now_iso()))


def upsert_scene(con, *, product_id: str, title: str, sensing_time: str,
                 orbit_direction: str | None, relative_orbit: int | None,
                 footprint_wkt: str | None) -> None:
    con.execute(
        """INSERT INTO scenes (product_id,title,sensing_time,orbit_direction,
                               relative_orbit,footprint_wkt,status,status_updated_at)
           VALUES (?,?,?,?,?,?, 'DISCOVERED', ?)
           ON CONFLICT(product_id) DO NOTHING""",
        (product_id, title, sensing_time, orbit_direction, relative_orbit,
         footprint_wkt, now_iso()))


def set_scene_status(con, product_id: str, status: str, raw_path: str | None = None) -> None:
    con.execute(
        "UPDATE scenes SET status=?, raw_path=COALESCE(?, raw_path), status_updated_at=? WHERE product_id=?",
        (status, raw_path, now_iso(), product_id))


def scenes_by_status(con, status: str) -> Iterable[sqlite3.Row]:
    return con.execute("SELECT * FROM scenes WHERE status=? ORDER BY sensing_time", (status,)).fetchall()


def coverage_summary(con) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT status, COUNT(*) n, MIN(sensing_time) t0, MAX(sensing_time) t1
           FROM scenes GROUP BY status""").fetchall()
