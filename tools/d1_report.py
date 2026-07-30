"""D1 — prove the pipes are real.

Prints, for every table this build lands: row count, date range, an AOI bounds
check, and size on disk. Then totals disk usage against the 1 GB budget.

Run it from the repo root:

    python tools/d1_report.py

**What a healthy run looks like:** each table shows a row count above zero, a
date range inside the window you pulled, `AOI: all inside` for anything with
positions, and a total comfortably under 1 GB.

**What a problem looks like:** `(not landed yet)` means that connector has not
been run. A row count of 0 means it ran but found nothing — for the GFW SAR
tables that is currently expected, because the upstream dataset is offline (see
DATA_SOURCES.md). `AOI: N outside` on any table is a real bug: the connectors
filter to the AOI, so anything outside it should never have landed.

Nothing here contacts the network. It only reads what is already on disk.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402

from maritime_isr.config import AOI_V1, cfg  # noqa: E402
from maritime_isr.ingest.landing import conformed_dir, table_day_partitions  # noqa: E402

DISK_BUDGET_BYTES = 1_000_000_000

# Conformed Parquet tables: (table, time column, has lat/lon)
PARQUET_TABLES = [
    ("gfw_encounters",         "start_time",             True),
    ("gfw_loitering",          "start_time",             True),
    ("gfw_port_visits",        "start_time",             True),
    ("gfw_ais_gaps",           "start_time",             True),
    ("gfw_sar_detections",     "acquired_at_detection",  True),
    ("gfw_sar_presence_grid",  "observed_date",          True),
    ("gfw_vessel_identity",    "valid_from",             False),
    ("gfw_vessel_owners",      "valid_from",             False),
    ("gfw_vessel_current",     "last_seen",              False),
]

# DuckDB tables written by the registries connector: (table, time column, has lat/lon)
DUCKDB_TABLES = [
    ("ofac_sdn",           "as_of", False),
    ("un_consolidated",    "as_of", False),
    ("eu_consolidated",    "as_of", False),
    ("wpi_ports",          "as_of", True),
    ("scene_catalog",      "acquired_at", False),
    ("registry_snapshots", "as_of", False),
]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def fmt_range(lo, hi) -> str:
    def one(v):
        if v is None:
            return "?"
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d")
        return str(v)[:10]
    return f"{one(lo)} .. {one(hi)}"


def line(name: str, rows: str, dates: str, aoi: str, size: str) -> str:
    return f"  {name:<24} {rows:>9}  {dates:<24} {aoi:<18} {size:>10}"


def report_parquet(con) -> tuple[int, int]:
    print("\nCONNECTOR OUTPUTS  (data/conformed/<table>/day=*/part.parquet)")
    print("  " + "-" * 92)
    print(line("table", "rows", "date range", "AOI check", "on disk"))
    print("  " + "-" * 92)

    total_rows = total_bytes = 0
    for table, tcol, has_pos in PARQUET_TABLES:
        parts = table_day_partitions(table)
        size = dir_size(conformed_dir(table))
        if not parts:
            print(line(table, "-", "(not landed yet)", "-", "-"))
            continue

        glob = str(conformed_dir(table) / "day=*" / "part.parquet")
        try:
            n = con.execute(
                f"SELECT count(*) FROM read_parquet('{glob}', union_by_name=true)"
            ).fetchone()[0]
        except duckdb.Error as e:
            print(line(table, "ERR", str(e)[:22], "-", human(size)))
            continue

        try:
            lo, hi = con.execute(
                f'SELECT min("{tcol}"), max("{tcol}") '
                f"FROM read_parquet('{glob}', union_by_name=true)"
            ).fetchone()
            dates = fmt_range(lo, hi)
        except duckdb.Error:
            dates = "(no time column)"

        aoi = "n/a (no position)"
        if has_pos:
            try:
                outside = con.execute(
                    f"SELECT count(*) FROM read_parquet('{glob}', union_by_name=true) "
                    f"WHERE lat IS NOT NULL AND lon IS NOT NULL AND NOT ("
                    f"lat BETWEEN {AOI_V1.lat_min} AND {AOI_V1.lat_max} AND "
                    f"lon BETWEEN {AOI_V1.lon_min} AND {AOI_V1.lon_max})"
                ).fetchone()[0]
                aoi = "all inside" if outside == 0 else f"** {outside} OUTSIDE **"
            except duckdb.Error:
                aoi = "(no lat/lon)"

        print(line(table, f"{n:,}", dates, aoi, human(size)))
        total_rows += n
        total_bytes += size

    return total_rows, total_bytes


def report_duckdb(con) -> tuple[int, int]:
    print("\nREGISTRY / CATALOG TABLES  (data/misr.duckdb, data/catalog.sqlite)")
    print("  " + "-" * 92)
    print(line("table", "rows", "date range", "AOI check", "on disk"))
    print("  " + "-" * 92)

    total_rows = 0
    for table, tcol, has_pos in DUCKDB_TABLES:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        if not exists:
            print(line(table, "-", "(not landed yet)", "-", "-"))
            continue

        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        try:
            lo, hi = con.execute(f'SELECT min("{tcol}"), max("{tcol}") FROM {table}').fetchone()
            dates = fmt_range(lo, hi)
        except duckdb.Error:
            dates = "(no time column)"

        aoi = "n/a (no position)"
        if has_pos:
            try:
                outside = con.execute(
                    f"SELECT count(*) FROM {table} WHERE lat IS NOT NULL AND NOT ("
                    f"lat BETWEEN {AOI_V1.lat_min} AND {AOI_V1.lat_max} AND "
                    f"lon BETWEEN {AOI_V1.lon_min} AND {AOI_V1.lon_max})"
                ).fetchone()[0]
                # WPI is deliberately global — ports outside the AOI are expected.
                aoi = "all inside" if outside == 0 else f"{outside} outside (ok: global list)"
            except duckdb.Error:
                aoi = "(no lat/lon)"

        print(line(table, f"{n:,}", dates, aoi, "in db"))
        total_rows += n

    return total_rows, 0


def report_snapshots(con) -> None:
    """Sanctions must be versioned, not overwritten — show the snapshot history."""
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='registry_snapshots'"
    ).fetchone()[0]
    if not exists:
        return
    rows = con.execute(
        "SELECT source_id, count(*) AS versions, min(as_of), max(as_of) "
        "FROM registry_snapshots GROUP BY source_id ORDER BY source_id"
    ).fetchall()
    if not rows:
        return
    print("\nREGISTRY SNAPSHOT HISTORY  (versioned, never overwritten)")
    print("  " + "-" * 92)
    print(f"  {'source':<24} {'versions':>9}  {'first':<12} {'latest':<12}")
    print("  " + "-" * 92)
    for src, versions, first, latest in rows:
        print(f"  {src:<24} {versions:>9}  {str(first)[:10]:<12} {str(latest)[:10]:<12}")


def report_coverage(con) -> int:
    """Non-null rate for every declared field. Returns the number of failures.

    A row count says a table has rows. It does not say the rows carry values —
    and an all-null column looks perfect in every count we print elsewhere.
    Floors marked `-` are observed and not gated: they have not been measured on
    live data yet, and inventing a bar in a sandbox is the same class of error
    this section exists to catch.
    """
    from maritime_isr.ingest.checks import COVERAGE_EXPECTATIONS, coverage_report

    printed = False
    failures = 0
    for table in sorted(COVERAGE_EXPECTATIONS):
        pattern = str(cfg.data_root / "conformed" / table / "day=*" / "part.parquet")
        try:
            rows = con.execute(
                f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
            ).fetchdf().to_dict("records")
        except Exception:  # noqa: BLE001 — table not landed yet
            continue
        if not rows:
            continue
        if not printed:
            print("\nFIELD COVERAGE  (non-null rate; '-' = observed, not gated)")
            print("  " + "-" * 92)
            printed = True
        print(f"  {table}  ({len(rows):,} rows)")
        for field, rate, floor in coverage_report(table, rows):
            bar = f">= {floor:.0%}" if floor is not None else "-"
            bad = floor is not None and rate < floor
            if bad:
                failures += 1
            mark = "  FAIL" if bad else ""
            print(f"      {field:<32} {rate:>7.1%}   {bar:>7}{mark}")
    if printed and not failures:
        print("  no floored field fell below its bar.")
    return failures


def main() -> int:
    print("=" * 96)
    print("Maritime ISR — D1 landed-data report")
    print("=" * 96)
    print(f"AOI       : {AOI_V1.name}   "
          f"{AOI_V1.lat_min}-{AOI_V1.lat_max}N   {AOI_V1.lon_min}-{AOI_V1.lon_max}E")
    print(f"data root : {cfg.data_root.resolve()}")
    print(f"backend   : {cfg.store_backend}")

    con = duckdb.connect(str(cfg.duckdb_path()))
    try:
        p_rows, p_bytes = report_parquet(con)
        d_rows, _ = report_duckdb(con)
        report_snapshots(con)
        coverage_failures = report_coverage(con)
    finally:
        con.close()

    raw_bytes = dir_size(cfg.data_root / "raw")
    total_bytes = dir_size(cfg.data_root)
    pct = 100.0 * total_bytes / DISK_BUDGET_BYTES

    print("\nDISK USAGE")
    print("  " + "-" * 92)
    print(f"  raw (immutable)          {human(raw_bytes):>12}")
    print(f"  conformed parquet        {human(p_bytes):>12}")
    print(f"  everything under data/   {human(total_bytes):>12}")
    print(f"  budget                   {human(DISK_BUDGET_BYTES):>12}   "
          f"({pct:.1f}% used)")

    print("\nTOTALS")
    print("  " + "-" * 92)
    print(f"  connector output rows    {p_rows:>12,}")
    print(f"  registry/catalog rows    {d_rows:>12,}")

    print("\n" + "=" * 96)
    if total_bytes > DISK_BUDGET_BYTES:
        print("OVER BUDGET — prune old snapshots or raw pulls before the next run.")
    elif p_rows == 0 and d_rows == 0:
        print("Nothing landed yet. Run the connectors first — see README 'D1 quickstart'.")
    else:
        print("Within budget.")
    if coverage_failures:
        print(f"{coverage_failures} field(s) below their coverage floor — see FIELD "
              "COVERAGE above. Row counts do not reveal this; treat it as a landing bug.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
