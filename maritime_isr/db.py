"""DuckDB access layer. One place that knows how to point DuckDB at the stores.

Readers call connect() then query registered views (ais, detections) and the
scene_catalog table. Views are Parquet globs from store.glob_for_reader, so
switching MISR_STORE_BACKEND changes nothing here.
"""
from __future__ import annotations

import os
from typing import Optional

import duckdb

from .config import cfg
from .store import glob_for_reader

PARQUET_STORES = ("ais", "detections")


def _wire_r2(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSTALL httpfs; LOAD httpfs;")
    account = os.getenv("R2_ACCOUNT_ID")
    con.execute("SET s3_url_style='path';")
    if account:
        con.execute(f"SET s3_endpoint='{account}.r2.cloudflarestorage.com';")
    if os.getenv("R2_ACCESS_KEY_ID"):
        con.execute(f"SET s3_access_key_id='{os.environ['R2_ACCESS_KEY_ID']}';")
    if os.getenv("R2_SECRET_ACCESS_KEY"):
        con.execute(f"SET s3_secret_access_key='{os.environ['R2_SECRET_ACCESS_KEY']}';")
    con.execute("SET s3_region='auto';")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    cfg.duckdb_path().parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.duckdb_path()), read_only=read_only)
    if cfg.store_backend in ("r2", "mirror"):
        try:
            _wire_r2(con)
        except Exception:
            pass  # offline / no creds — local reads still work
    _register_views(con)
    return con


def _register_views(con: duckdb.DuckDBPyConnection) -> None:
    for store in PARQUET_STORES:
        pattern = glob_for_reader(store)
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW {store} AS "
                f"SELECT * FROM read_parquet('{pattern}', union_by_name=true, filename=true)"
            )
        except duckdb.Error:
            # no partitions yet — expose an empty stub so queries don't crash
            con.execute(f"CREATE OR REPLACE VIEW {store} AS SELECT NULL WHERE false")


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    r = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(r and r[0])


def ensure_scene_catalog(con: duckdb.DuckDBPyConnection) -> None:
    """Scene catalog is a real table (mutable status), not a Parquet view."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_catalog (
            scene_id VARCHAR PRIMARY KEY,
            footprint_wkt VARCHAR,
            orbit_direction VARCHAR,
            relative_orbit INTEGER,
            acquired_at TIMESTAMPTZ,
            mode VARCHAR,
            polarizations VARCHAR,
            status VARCHAR,
            status_detail VARCHAR,
            raw_uri VARCHAR,
            calibrated_uri VARCHAR,
            source_id VARCHAR,
            source_ref VARCHAR,
            provenance_acquired_at TIMESTAMPTZ,
            ingested_at TIMESTAMPTZ,
            pipeline_version VARCHAR,
            confidence DOUBLE
        )
        """
    )


def scene_count(con: duckdb.DuckDBPyConnection, status: Optional[str] = None) -> int:
    if not table_exists(con, "scene_catalog"):
        return 0
    if status:
        r = con.execute("SELECT count(*) FROM scene_catalog WHERE status = ?", [status]).fetchone()
    else:
        r = con.execute("SELECT count(*) FROM scene_catalog").fetchone()
    return int(r[0]) if r else 0
