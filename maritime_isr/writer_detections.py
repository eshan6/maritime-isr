"""Detection Parquet writer. Detections partition by acquisition hour, same
discipline as AIS but keyed on detection_id for dedup (re-running detection over
a scene never duplicates contacts).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from .store import local_partition_path


def _hour_key(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def write_detections(rows: Iterable[dict], store: str = "detections") -> dict[str, int]:
    by_hour: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_hour[_hour_key(r["acquired_at"])].append(r)
    written: dict[str, int] = {}
    for hour, hrows in by_hour.items():
        path = local_partition_path(store, hour)
        merged = _merge_dedup(path, hrows)
        pq.write_table(pa.Table.from_pylist(merged), path, compression="zstd")
        written[hour] = len(merged)
    return written


def _merge_dedup(path: Path, new_rows: list[dict]) -> list[dict]:
    existing = pq.read_table(path).to_pylist() if path.exists() else []
    seen = {r["detection_id"]: r for r in existing + new_rows}
    return list(seen.values())
