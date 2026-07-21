"""Conformed layer writer: partitioned parquet + catalog registration in
one call, so nothing lands unconformed or uncataloged."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CONFORMED_ROOT, PIPELINE_VERSION
from . import catalog as cat


def write(table: pa.Table, dataset: str, *, source: str, aoi: str | None,
          partition_day: str) -> Path:
    d = CONFORMED_ROOT / dataset / f"day={partition_day}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{source.replace(':', '_')}.parquet"
    pq.write_table(table, path, compression="zstd")
    ts_col = "ts" if "ts" in table.column_names else "acquired_at"
    t0 = t1 = None
    if table.num_rows and ts_col in table.column_names:
        col = table.column(ts_col).to_pylist()
        vals = [v for v in col if v is not None]
        if vals:
            t0, t1 = min(vals).isoformat(), max(vals).isoformat()
    with cat.connect() as con:
        cat.register_artifact(con, source=source, kind="conformed", path=str(path),
                              sha256=None, t_start=t0, t_end=t1, aoi=aoi,
                              pipeline_version=PIPELINE_VERSION)
    return path
