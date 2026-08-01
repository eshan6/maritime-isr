"""Add the missing H3 resolutions to landed tables, computed from lat/lon.

    python tools/restamp_h3.py --dry-run     # report only, writes nothing
    python tools/restamp_h3.py               # rewrite the partitions

**Why this is needed.** ADR-015 requires every located row to carry a cell at
all five project resolutions (4, 6, 7, 8, 9), each computed directly from
lat/lon, because the fusion core joins at res 6 while ingest used to stamp only
7 and 9 — so ingest and fusion tables could not be joined at all.

The code was fixed. **The landed data was not**, and it cannot be: the rows on
the operator's machine were written before the fix. Measured on that corpus,
`gfw_encounters` carries `h3_r7` and `h3_r9` and nothing else. So the join
ADR-015 exists to enable still returns nothing, and will keep returning nothing
until the rows are restamped.

**Why recomputing is legitimate rather than a patch of the conformed layer.**
An H3 cell is a pure function of latitude, longitude and resolution. Deriving
`h3_r6` from the row's own coordinates produces exactly what re-running the
connector would produce — there is no upstream information involved and no
judgement being applied. That is different in kind from editing a value.

The alternative, re-running the connectors, needs network access to GFW and
would return a *different* window of events, which is worse: it changes the
corpus while trying to fix a column.

**What this will not do.** It never derives a coarse cell from a fine one.
ADR-015 measured that at 7.2% disagreement, and `h3util` deliberately offers no
parent function. Every resolution here comes from the coordinates.

Rows without a usable position are left alone — a port visit keyed only to a
port id has no coordinate of its own.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa                                             # noqa: E402
import pyarrow.parquet as pq                                     # noqa: E402

from maritime_isr.h3util import RESOLUTIONS, cell                # noqa: E402
from maritime_isr.ingest.landing import (conformed_dir,          # noqa: E402
                                         table_day_partitions)

#: Tables that carry coordinates and are read by the fusion core or the graph.
TABLES = ("gfw_encounters", "gfw_loitering", "gfw_port_visits", "gfw_ais_gaps",
          "ais_position", "scenario_detections")


def restamp_table(table: str, *, dry_run: bool) -> dict:
    parts = table_day_partitions(table)
    if not parts:
        return dict(table=table, partitions=0, rows=0, positioned=0,
                    added=0, corrected=0, note="table absent")

    n_rows = n_pos = n_added = n_corrected = 0
    n_written = 0

    for path in parts:
        try:
            rows = pq.read_table(path).to_pylist()
        except Exception as exc:                              # noqa: BLE001
            print(f"  {path}: unreadable ({exc})")
            continue
        changed = False
        for r in rows:
            n_rows += 1
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or lon is None:
                continue
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            n_pos += 1
            for res in RESOLUTIONS:
                col = f"h3_r{res}"
                want = cell(lat, lon, res)
                got = r.get(col)
                if got is None:
                    r[col] = want
                    n_added += 1
                    changed = True
                elif got != want:
                    # A present-but-wrong cell is the ADR-015 defect in its
                    # nastiest form: the column looks populated and the join
                    # still misses. Correct it and count it separately, because
                    # a non-zero number here means something derived a cell
                    # instead of computing it.
                    r[col] = want
                    n_corrected += 1
                    changed = True
        if changed and not dry_run:
            pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")
            n_written += 1

    return dict(table=table, partitions=len(parts), rows=n_rows,
                positioned=n_pos, added=n_added, corrected=n_corrected,
                rewritten=n_written)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    args = ap.parse_args()

    print("H3 restamp — every resolution computed from lat/lon (ADR-015)")
    if args.dry_run:
        print("DRY RUN: nothing will be written.\n")
    else:
        print("Writing. Back up data/conformed first if you have not.\n")

    print(f"  {'table':<26}{'parts':>6}{'rows':>10}{'positioned':>12}"
          f"{'cells added':>13}{'corrected':>11}")
    total_added = total_corrected = 0
    for table in TABLES:
        r = restamp_table(table, dry_run=args.dry_run)
        if r.get("note"):
            print(f"  {table:<26}{'-':>6}{'-':>10}{'-':>12}"
                  f"{'-':>13}{'-':>11}  ({r['note']})")
            continue
        print(f"  {table:<26}{r['partitions']:>6}{r['rows']:>10,}"
              f"{r['positioned']:>12,}{r['added']:>13,}{r['corrected']:>11,}")
        total_added += r["added"]
        total_corrected += r["corrected"]

    print()
    if total_corrected:
        print(f"WARNING: {total_corrected:,} cell(s) were present but WRONG. "
              f"A present-but-wrong H3 cell means something derived it from "
              f"another resolution instead of computing it from coordinates — "
              f"exactly the ~7% disagreement ADR-015 measured. Worth finding "
              f"the writer.")
    if args.dry_run:
        print(f"{total_added:,} cell(s) would be added. Re-run without "
              f"--dry-run to apply.")
    else:
        print(f"{total_added:,} cell(s) added. Ingest and fusion tables can "
              f"now join at res 6, which is what ADR-015 was written for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
