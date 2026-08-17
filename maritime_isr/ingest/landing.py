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
from ..h3util import index_all, index_both
from ..schemas import git_sha, utcnow

__all__ = [
    "land_raw",
    "land_table",
    "conformed_dir",
    "read_table",
    "split_real_synthetic",
    "table_day_partitions",
    "stamp_envelope",
    "SYNTHETIC_SOURCE_ID",
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

#: The one source_id scenario data ever carries. Defined here rather than
#: imported from `scenario/` so the ingest layer has no dependency on the
#: generator — this module must be able to enforce the rule even in a checkout
#: where the scenario package is absent.
SYNTHETIC_SOURCE_ID = "synthetic-scenario"


def stamp_envelope(
    row: dict,
    *,
    source_id: str,
    source_ref: str,
    acquired_at: datetime,
    confidence: float | None = None,
    is_synthetic: bool = False,
) -> dict:
    """Attach the provenance envelope to a row, in place.

    `acquired_at` is when the phenomenon was observed, not when we fetched it —
    those differ, and conflating them destroys the ability to reason about how
    stale a fact is.

    **`is_synthetic` and `source_id` must agree, and this is where that is
    enforced.** Scenario data lives in the same tables as real data so that it
    exercises the identical code path (ADR-019), which means the *only* thing
    keeping the two apart is this flag. Two independent markers — a boolean and
    a source id — are safer than one, but only if they can never drift: a row
    flagged synthetic with a real source id, or vice versa, would make every
    "real vs synthetic" split silently wrong in a way no row count would reveal.
    So the disagreement is refused at the point of stamping rather than
    detected later.
    """
    if acquired_at.tzinfo is None:
        raise ValueError("acquired_at must be timezone-aware UTC")
    if is_synthetic and source_id != SYNTHETIC_SOURCE_ID:
        raise ValueError(
            f"is_synthetic=True requires source_id={SYNTHETIC_SOURCE_ID!r}, "
            f"got {source_id!r} — the flag and the envelope must agree")
    if not is_synthetic and source_id == SYNTHETIC_SOURCE_ID:
        raise ValueError(
            f"source_id={SYNTHETIC_SOURCE_ID!r} requires is_synthetic=True — "
            f"the flag and the envelope must agree")
    row["source_id"] = source_id
    row["source_ref"] = source_ref
    row["acquired_at"] = acquired_at.astimezone(timezone.utc)
    row["ingested_at"] = utcnow()
    row["pipeline_version"] = git_sha()
    row["confidence"] = confidence
    row["is_synthetic"] = bool(is_synthetic)
    return row


def stamp_h3(row: dict, lat_key: str = "lat", lon_key: str = "lon") -> dict:
    """Stamp a cell for EVERY project resolution (4, 6, 7, 8, 9) onto a row.

    Previously this stamped only res 7 and 9, while the fusion core joins on
    res 6 — so ingest tables and fusion tables could not be joined at all
    (ADR-015). Stamping all of them removes the mismatch permanently and, more
    importantly, removes any future temptation to derive a coarse cell from a
    fine one, which disagrees with direct computation for ~7% of positions.

    Each resolution is computed independently from lat/lon. The cost is a few
    short strings per row — negligible against the join correctness it buys.

    Rows without a usable position are left alone: a port visit keyed only to a
    port id has no coordinate of its own until the port table is joined.
    """
    lat, lon = row.get(lat_key), row.get(lon_key)
    if lat is None or lon is None:
        return row
    try:
        row.update(index_all(float(lat), float(lon)))
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


class LandResult(dict):
    """{day: rows this call landed} — plus what it replaced and what is there now.

    A plain `dict[str, int]` of per-day counts is what every caller already
    sums and what `checks.landed()` adds up, so this stays one and nothing
    downstream has to change. The two extra attributes hang off the side
    because the landed count alone cannot answer the question an operator
    actually asks when they re-run a connector and see the same output twice:
    *did anything change?*

    - `replaced` — of the rows this call landed, how many overwrote a row of
      the same natural key already on disk. All of them means the re-run
      converged, which is the connector working, not the connector stuck.
    - `partition_rows` — how many rows the partition holds now, from every
      producer. Useful, and deliberately not the headline: it is the number
      that used to be printed as "landed".
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.replaced: dict[str, int] = {}
        self.partition_rows: dict[str, int] = {}


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

    Returns a `LandResult`: {day: rows *this call* landed}.

    **It used to return the partition's size after the merge, and that was a
    lie every caller repeated.** Everything downstream sums these values and
    prints the total as "landed", so a connector writing into a partition that
    already held rows announced them as its own. Importing five territorial
    seas into the day partition holding the 63 derived zones reported `landed
    68`, and `report_landed` then computed `5 - 68` and offered `-63 merged`.
    A negative provenance count is the visible end of a systemic error: the
    number an operator carries away has to be what this run put on disk.
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

    written = LandResult()
    for day, drows in by_day.items():
        path = _partition_path(table, day)
        existing: list[dict] = []
        if path.exists():
            existing = pq.read_table(path).to_pylist()

        before = {_natural_key(r, key_fields) for r in existing}
        mine: dict[str, dict] = {}
        for r in drows:
            mine[_natural_key(r, key_fields)] = r

        merged: dict[str, dict] = {}
        for r in existing + drows:
            merged[_natural_key(r, key_fields)] = r

        out = _normalise_for_arrow(list(merged.values()))
        pq.write_table(pa.Table.from_pylist(out), path, compression="zstd")

        written[day] = len(mine)
        written.replaced[day] = len(before & mine.keys())
        written.partition_rows[day] = len(out)

    reconcile_null_columns(table)
    return written


# --------------------------------------------------------------------------
# schema reconciliation across partitions
# --------------------------------------------------------------------------

def _type_hints(table: str) -> dict[str, "pa.DataType"]:
    """Column -> the concrete Arrow type some partition of `table` uses."""
    hints: dict[str, pa.DataType] = {}
    for p in table_day_partitions(table):
        try:
            schema = pq.read_schema(p)
        except Exception:                                      # noqa: BLE001
            continue
        for f in schema:
            if f.name not in hints and not pa.types.is_null(f.type):
                hints[f.name] = f.type
    return hints


def reconcile_null_columns(table: str) -> int:
    """Give all-null columns the type their siblings use. Returns partitions fixed.

    **Why this is not optional.** Arrow infers a column's type from the values
    present, so a day partition where an optional column happens to be null in
    every row gets that column typed `null`. A sibling partition with one real
    value types it `double`. Both files are individually fine; read together —
    which is how every query in this project reads them — DuckDB takes the
    schema from the first file and fails outright:

        Conversion Error: failed to cast column "dwell_hours" from type DOUBLE
        to "NULL"

    So one sparse column silently makes an entire table unqueryable, and *which*
    column depends on which day's rows happened to be empty. That is a landmine
    with a fuse measured in weeks: it does not fire when the column is added, it
    fires the first time a partition comes out all-null.

    The fix is to rewrite those partitions with the type its siblings agree on.
    A null-typed column carries no values, so retyping it cannot change data —
    it only stops the file lying about what it holds. Partitions with nothing to
    fix are left alone, so this costs a schema read per partition and no writes
    in the normal case.
    """
    hints = _type_hints(table)
    if not hints:
        return 0
    fixed = 0
    for p in table_day_partitions(table):
        try:
            tbl = pq.read_table(p)
        except Exception:                                      # noqa: BLE001
            continue
        bad = [f.name for f in tbl.schema
               if pa.types.is_null(f.type) and f.name in hints]
        if not bad:
            continue
        tbl = tbl.cast(pa.schema([
            pa.field(f.name, hints[f.name]) if f.name in bad else f
            for f in tbl.schema]))
        pq.write_table(tbl, p, compression="zstd")
        fixed += 1
    return fixed


# --------------------------------------------------------------------------
# reading back
# --------------------------------------------------------------------------

def table_glob(table: str) -> str:
    return str(conformed_dir(table) / "day=*" / "part.parquet")


def table_day_partitions(table: str) -> list[Path]:
    return sorted(conformed_dir(table).glob("day=*/part.parquet"))


def read_table(table: str) -> list[dict]:
    """Read every partition of a conformed table. Small tables only.

    **A partition written before `is_synthetic` existed has no such column, and
    its rows are real.** Defaulting the missing value to False here is what
    makes the migration zero-recompute: no existing partition is rewritten, and
    every consumer still sees a populated flag on every row. A reader that left
    it as None would push the same decision onto every call site, and one of
    them would eventually get it wrong.
    """
    rows: list[dict] = []
    for p in table_day_partitions(table):
        for r in pq.read_table(p).to_pylist():
            if r.get("is_synthetic") is None:
                r["is_synthetic"] = False
            rows.append(r)
    return rows


def split_real_synthetic(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(real, synthetic). The split every externally quotable count needs.

    Kept here rather than written out at each call site so that "how many of
    these are real?" always has one answer computed one way.
    """
    real = [r for r in rows if not r.get("is_synthetic")]
    syn = [r for r in rows if r.get("is_synthetic")]
    return real, syn
