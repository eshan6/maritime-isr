"""DuckDB read layer for the API.

One connection knows how to reach both stores the API reads from DuckDB:

  * the conformed Parquet tables under ``data/conformed/<table>/day=*/part.parquet``,
    exposed as temp views named after the table, and
  * the DuckDB-resident registry tables (``ofac_sdn``, ``scene_catalog`` …) that
    ``ingest/registries.py`` writes into ``misr.duckdb``.

The object graph is SQLite and is read through :class:`GraphStore`, not here.

**A fresh connection per request.** DuckDB connections are not safe to share
across the threads FastAPI runs sync handlers on, and registering a dozen
Parquet globs is cheap, so each request opens and closes its own reader. See
:func:`open_reader`.

**statistics_propagation is disabled** for the same reason the profiler disables
it: DuckDB constant-folds ``min()/max()`` from Parquet column statistics at plan
time and hits an internal assertion on some single-partition files. It is a
planner bug, not a data problem; turning that one optimizer off makes those
aggregates run normally at execution time.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import duckdb

from ..config import cfg

#: Conformed tables the API knows how to surface. A table absent from disk is
#: simply not registered — the endpoints degrade to empty rather than 500.
CONFORMED_TABLES = (
    "ais_position",
    # AIS message 5 — what each hull declared about her voyage (ADR-035). The
    # generator wrote this table before it was listed here, and the effect was
    # the silent one this tuple always risks: `has()` answered False, the
    # voyage rule was handed an empty list, and it reported no findings over a
    # corpus that contained three. A table absent from this list is a table the
    # serving layer cannot see, whatever is on disk.
    "ais_voyage",
    # Arrival notifications, extracted from the documents in the inbox
    # (ADR-036). The documents themselves are not a table — they are inputs.
    "arrival_notification",
    "gfw_vessel_identity",
    "gfw_encounters",
    "gfw_loitering",
    "gfw_port_visits",
    "gfw_ais_gaps",
    "sanctioned_vessel_matches",
    "sar_imaging_opportunity",
    "scenario_detections",
    "scenario_organizations",
    "scenario_ownership",
    "scenario_sanctions",
    # NB: scenario_truth is deliberately NOT here. It is evaluation ground truth
    # and no serving, detection or scoring code may read it (ADR-019 §d) — the
    # product must never show an operator the answer key.
)


def conformed_glob(table: str) -> str:
    return str(cfg.data_root / "conformed" / table / "day=*" / "part.parquet")


def conformed_present(table: str) -> bool:
    return any((cfg.data_root / "conformed" / table).glob("day=*/part.parquet"))


class Reader:
    """A thin wrapper over a DuckDB connection with the views registered."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self._present: set[str] = set()

    def has(self, table: str) -> bool:
        """Is `table` queryable on this connection (a registered view or a
        native DuckDB table with at least its schema)?"""
        if table in self._present:
            return True
        r = self.con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()
        return bool(r and r[0])

    def columns(self, table: str) -> set[str]:
        """Column names of a table/view, or empty set if it can't be read.

        The real and scenario corpora do **not** always share a schema — the
        real `sanctioned_vessel_matches` has no `is_synthetic` column, for
        instance — so any query that references an optional column must check
        here first rather than assume it exists and 500 on the real data.
        """
        try:
            return {r[0] for r in
                    self.con.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()}
        except duckdb.Error:
            return set()

    def rows(self, sql: str, params: list | None = None) -> list[dict]:
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def one(self, sql: str, params: list | None = None) -> dict | None:
        r = self.rows(sql, params)
        return r[0] if r else None

    def scalar(self, sql: str, params: list | None = None):
        r = self.con.execute(sql, params or []).fetchone()
        return r[0] if r else None


def _register(con: duckdb.DuckDBPyConnection) -> set[str]:
    present: set[str] = set()
    for table in CONFORMED_TABLES:
        if not conformed_present(table):
            continue
        glob = conformed_glob(table).replace("'", "''")
        try:
            con.execute(
                f"CREATE OR REPLACE TEMP VIEW {table} AS "
                f"SELECT * FROM read_parquet('{glob}', union_by_name=true)"
            )
            present.add(table)
        except duckdb.Error:
            # A malformed partition should not take down the whole API; the
            # table is simply left unregistered and its endpoint returns empty.
            continue
    return present


@contextmanager
def open_reader() -> Iterator[Reader]:
    """Open a DuckDB connection with conformed views + registry tables ready.

    Reads `misr.duckdb` read-only when it exists (that is where the sanctions
    registries and the scene catalog live); falls back to an in-memory engine
    when it does not, which still serves the Parquet-backed endpoints.
    """
    db_path: Path = cfg.duckdb_path()
    con: duckdb.DuckDBPyConnection
    if db_path.exists():
        try:
            con = duckdb.connect(str(db_path), read_only=True)
        except duckdb.Error:
            con = duckdb.connect()
    else:
        con = duckdb.connect()
    try:
        con.execute("SET disabled_optimizers='statistics_propagation'")
    except duckdb.Error:
        pass
    reader = Reader(con)
    reader._present = _register(con)
    try:
        yield reader
    finally:
        con.close()


def as_iso(value) -> str | None:
    """DuckDB returns tz-aware datetimes; the API always emits ISO-8601 UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
