"""Shared landing layer for D1 connector outputs.

One place that knows how to put a connector's rows on disk, so every connector
lands identically and the fusion core never learns a source-specific hack
(CLAUDE.md §4.5). A connector's only job is to map its source into canonical
rows; this module does the rest:

  * **Raw is landed first and never mutated.** The exact bytes the source
    returned go to `data/raw/<source>/<day>/<name>`, content-addressed by SHA-256.
    Every conformed row can be re-derived from raw plus the git SHA.
  * **Provenance envelope on every row.** source_id, source_ref, acquired_at,
    ingested_at, pipeline_version (git SHA), confidence. No row lands without it.
  * **H3 stamping at ingest.** Any row carrying lat/lon gets its res-7 and res-9
    cells from the one shared helper, so spatial joins are hash joins later.
  * **Idempotent on re-run.** Rows carry a natural key; re-landing the same
    window merges rather than duplicates. Running a backfill twice is a no-op.
  * **Day-partitioned Parquet** under `data/conformed/<table>/day=YYYY-MM-DD/`.

Tables land under `conformed/`, separate from the hourly `parquet/ais` and
`parquet/detections` stores that the live path writes, because these are
connector outputs on a daily cadence rather than a streaming feed.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import cfg
from ..h3util import index_both
from ..schemas import git_sha, utcnow

__all__ = [
    "land_raw",
    "land_table",
    "conformed_dir",
    "read_table",
    "table_day_partitions",
    "stamp_envelope",
]


# --------------------------------------------------------------------------
# raw landing — immutable
# --------------------------------------------------------------------------

def land_raw(source: str, name: str, payload: bytes, *, day: str | None = None) -> tuple[Path, str]:
    """Write the exact source bytes to the immutable raw store.

    Returns (path, sha256). If a file with the same content already exists at
    that path we leave it alone — re-running a pull does not rewrite raw.
    """
    day = day or utcnow().strftime("%Y-%m-%d")
    sha = hashlib.sha256(payload).hexdigest()
    d = cfg.data_root / "raw" / source / f"day={day}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
        return path, sha
    path.write_bytes(payload)
    return path, sha


def land_raw_json(source: str, name: str, obj: Any, *, day: str | None = None) -> tuple[Path, str]:
    """Convenience wrapper for JSON API responses."""
    payload = json.dumps(obj, sort_keys=True, default=str, indent=None).encode("utf-8")
    return land_raw(source, name, payload, day=day)


# --------------------------------------------------------------------------
# provenance + H3
# --------------------------------------------------------------------------

def stamp_envelope(
    row: dict,
    *,
    source_id: str,
    source_ref: str,
    acquired_at: datetime,
    confidence: float | None = None,
) -> dict:
    """Attach the six-column provenance envelope to a row, in place.

    `acquired_at` is when the phenomenon was observed, not when we fetched it —
    those differ, and conflating them destroys the ability to reason about how
    stale a fact is.
    """
    if acquired_at.tzinfo is None:
        raise ValueError("acquired_at must be timezone-aware UTC")
    row["source_id"] = source_id
    row["source_ref"] = source_ref
    row["acquired_at"] = acquired_at.astimezone(timezone.utc)
    row["ingested_at"] = utcnow()
    row["pipeline_version"] = git_sha()
    row["confidence"] = confidence
    return row


def stamp_h3(row: dict, lat_key: str = "lat", lon_key: str = "lon") -> dict:
    """Stamp h3_r7/h3_r9 from a row's position, if it has one.

    Rows without a usable position are left alone — a port visit keyed only to a
    port id has no coordinate of its own until the port table is joined.
    """
    lat, lon = row.get(lat_key), row.get(lon_key)
    if lat is None or lon is None:
        return row
    try:
        row["h3_r7"], row["h3_r9"] = index_both(float(lat), float(lon))
    except (TypeError, ValueError):
        pass
    return row


# --------------------------------------------------------------------------
# conformed landing — day-partitioned, idempotent
# --------------------------------------------------------------------------

def conformed_dir(table: str) -> Path:
    d = cfg.data_root / "conformed" / table
    d.mkdir(parents=True, exist_ok=True)
    return d


def _partition_path(table: str, day: str) -> Path:
    d = conformed_dir(table) / f"day={day}"
    d.mkdir(parents=True, exist_ok=True)
    return d / "part.parquet"


def _day_of(row: dict, day_key: str) -> str:
    v = row.get(day_key)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(v, str) and len(v) >= 10:
        return v[:10]
    return utcnow().strftime("%Y-%m-%d")


def _natural_key(row: dict, key_fields: Sequence[str]) -> str:
    parts = []
    for f in key_fields:
        v = row.get(f)
        if isinstance(v, datetime):
            v = v.astimezone(timezone.utc).isoformat()
        parts.append("" if v is None else str(v))
    return "|".join(parts)


def _normalise_for_arrow(rows: list[dict]) -> list[dict]:
    """Give every row the same key set so Arrow can infer one schema.

    Without this, a batch where an optional field is present in some rows and
    absent in others produces a ragged table or a type error.
    """
    all_keys: set[str] = set()
    for r in rows:
        all_keys.update(r.keys())
    out = []
    for r in rows:
        full = {k: r.get(k) for k in sorted(all_keys)}
        out.append(full)
    return out


def land_table(
    rows: Iterable[dict],
    *,
    table: str,
    key_fields: Sequence[str],
    day_field: str = "acquired_at",
) -> dict[str, int]:
    """Land connector rows into day-partitioned Parquet, idempotently.

    `key_fields` is the natural key: re-landing a row with the same key replaces
    it rather than appending, so re-running a backfill over an overlapping
    window converges instead of duplicating.

    Returns {day: row_count_in_that_partition_after_merge}.
    """
    rows = list(rows)
    if not rows:
        return {}

    missing_envelope = [
        c for c in ("source_id", "source_ref", "acquired_at", "pipeline_version")
        if c not in rows[0]
    ]
    if missing_envelope:
        raise ValueError(
            f"refusing to land {table}: rows lack provenance {missing_envelope}. "
            "Call stamp_envelope() before land_table() — CLAUDE.md §4.1."
        )

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[_day_of(r, day_field)].append(r)

    written: dict[str, int] = {}
    for day, drows in by_day.items():
        path = _partition_path(table, day)
        existing: list[dict] = []
        if path.exists():
            existing = pq.read_table(path).to_pylist()

        merged: dict[str, dict] = {}
        for r in existing + drows:
            merged[_natural_key(r, key_fields)] = r

        out = _normalise_for_arrow(list(merged.values()))
        pq.write_table(pa.Table.from_pylist(out), path, compression="zstd")
        written[day] = len(out)
    return written


# --------------------------------------------------------------------------
# reading back
# --------------------------------------------------------------------------

def table_glob(table: str) -> str:
    return str(conformed_dir(table) / "day=*" / "part.parquet")


def table_day_partitions(table: str) -> list[Path]:
    return sorted(conformed_dir(table).glob("day=*/part.parquet"))


def read_table(table: str) -> list[dict]:
    """Read every partition of a conformed table. Small tables only."""
    rows: list[dict] = []
    for p in table_day_partitions(table):
        rows.extend(pq.read_table(p).to_pylist())
    return rows
