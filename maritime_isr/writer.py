"""Canonical Parquet writer. Every connector lands rows through here so the
provenance envelope, H3 stamping, dedup, and hourly partitioning are identical
across sources (standing rule 5: fusion core never learns source-specific hacks).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from .h3util import index_both
from .store import local_partition_path


def _hour_key(ts: datetime) -> str:
    ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H")


def write_position_reports(rows: Iterable[dict], store: str = "ais") -> dict[str, int]:
    """Land AIS-like rows (dicts) into hourly Parquet partitions.

    Each row must have: mmsi, lat, lon, timestamp (tz-aware), plus optional
    kinematics and a nested/flattened provenance envelope. H3 indices are
    stamped here if absent.

    Returns {hour_key: rows *this call* landed}, deduped on the natural key.
    Not the partition's size after the merge — that counts rows this call never
    wrote and reports them as its own, which is the same defect `land_table`
    carried until an import announced 68 rows for 5.

    Dedup is per-partition on (mmsi, timestamp, rounded lat/lon): re-running a
    backfill over the same window never duplicates.
    """
    by_hour: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("h3_r7") is None or r.get("h3_r9") is None:
            r["h3_r7"], r["h3_r9"] = index_both(r["lat"], r["lon"])
        by_hour[_hour_key(r["timestamp"])].append(r)

    written: dict[str, int] = {}
    for hour, hrows in by_hour.items():
        path = local_partition_path(store, hour)
        merged, mine = _merge_dedup(path, hrows)
        table = pa.Table.from_pylist(merged)
        pq.write_table(table, path, compression="zstd")
        written[hour] = mine
    return written


def _merge_dedup(path: Path, new_rows: list[dict]) -> tuple[list[dict], int]:
    """Merge new rows with any existing partition, dedup on natural key.

    Returns the merged partition and how many distinct rows came from
    `new_rows` — the caller needs the second number to report what it landed
    rather than what happens to be sitting in the file.
    """
    existing: list[dict] = []
    if path.exists():
        existing = pq.read_table(path).to_pylist()

    def key(r: dict) -> str:
        ts = r["timestamp"]
        ts_iso = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        return f"{r['mmsi']}|{ts_iso}|{round(r['lat'], 4)}|{round(r['lon'], 4)}"

    seen: dict[str, dict] = {}
    for r in existing + new_rows:
        seen[key(r)] = r  # last-write-wins; identical rebroadcasts collapse
    return list(seen.values()), len({key(r) for r in new_rows})


def partition_stats(store: str, hour_key: str) -> dict:
    path = local_partition_path(store, hour_key)
    if not path.exists():
        return {"exists": False, "rows": 0}
    t = pq.read_table(path)
    return {"exists": True, "rows": t.num_rows, "path": str(path)}
