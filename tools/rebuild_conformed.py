"""Re-derive the conformed GFW event tables from the immutable raw store.

    python tools/rebuild_conformed.py --dry-run          # audit only, no writes
    python tools/rebuild_conformed.py                    # rewrite the tables
    python tools/rebuild_conformed.py --kind port_visits # one kind

**Why this exists.** The conformed GFW tables were written by a mapper the code
has since outgrown. Measured on the operator's corpus, `confidence` and
`gfw_confidence_raw` are null on **100%** of the 3,000 port visits — GFW's own
judgement about how much of each visit they actually saw was being dropped on
the floor — and the three anchorage records that say *where* the visit happened
were never landed at all. The mapper was fixed on 2026-07-29. The landed rows
were not, and cannot fix themselves.

The immediate motivation was port visits lasting 20,253 hours (2.3 years). The
first explanation was that those spans were visits GFW had stitched together
without observing a stop, so re-deriving them would separate a dwell from a
stitch. **That explanation was wrong, and this tool's own audit is what proved
it**: 100% of the real visits have an observed stop, 87% are structurally clean
dwells, and the clean-dwell p95 is still 828 days. See the ADR-020 correction,
and `tools/port_visit_forensics.py` for the investigation that replaced it.

The rebuild is still worth running — recovering GFW's confidence on 3,000 rows
is the point on its own — but it does not fix the durations, and `dwell_hours`
is a better-defined field rather than a smaller one.

**Why re-deriving is the right fix rather than a patch.** This is precisely the
case CLAUDE.md §4.2 exists for: raw is immutable and still on disk, so every
conformed row is regenerable from raw plus a git SHA. Running the current
`map_event` over the same bytes GFW returned produces the table the current code
would have produced, with no judgement applied and no value invented. Nothing is
clamped, and no duration is "corrected" — `duration_hours` keeps meaning exactly
what GFW said. What changes is that `dwell_hours` now exists beside it and is
populated only where the structure supports the claim.

**It needs no network**, which matters: ADR-013 puts this machine in
download-only mode, and re-running the connectors would in any case return a
*different* window of events — changing the corpus in the course of repairing a
column.

**What it will not touch.** Synthetic rows are carried through byte-for-byte.
They live in the same partitions as real rows by design (ADR-019), so a rebuild
that rewrote partitions wholesale would silently delete the scenario corpus. Any
real row with no matching raw record is also preserved, and reported loudly —
that count should be zero, and if it is not, raw is incomplete and the claim
that derived data is regenerable is weaker than this project believes.

`ingested_at` is preserved from the existing row where one exists: a
re-derivation is not a new ingest, and moving that timestamp would falsify how
stale the fact is. `pipeline_version` *is* restamped, because it names the code
that produced the row and different code produced it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa                                             # noqa: E402
import pyarrow.parquet as pq                                     # noqa: E402

from maritime_isr.config import cfg                              # noqa: E402
from maritime_isr.ingest.gfw_events import (EVENT_SPECS,         # noqa: E402
                                            SOURCE_ID, map_event)
from maritime_isr.ingest.landing import (_normalise_for_arrow,   # noqa: E402
                                         conformed_dir,
                                         reconcile_null_columns,
                                         table_day_partitions)

RAW_DIR = "raw"


# --------------------------------------------------------------------------
# reading raw
# --------------------------------------------------------------------------

def raw_files(kind: str) -> list[Path]:
    """Every landed raw payload for one event kind, oldest day first.

    `fetch_kind` names them `<kind>_<start>_<end>.json` under
    `data/raw/gfw-events/day=YYYY-MM-DD/`.
    """
    root = cfg.data_root / RAW_DIR / SOURCE_ID
    if not root.exists():
        return []
    return sorted(root.glob(f"day=*/{kind}_*.json"))


def raw_events(kind: str) -> tuple[dict[str, dict], dict]:
    """Load raw events for one kind, keyed by GFW event id.

    Overlapping pulls are normal — the connector re-runs over a rolling window
    — so the same event appears in several files. The later file wins, which is
    the same convergence rule `land_table` applies, and the overlap is counted
    rather than hidden so a suspiciously low unique count is visible.
    """
    by_id: dict[str, dict] = {}
    stats = dict(files=0, records=0, unreadable=0, no_id=0)
    for path in raw_files(kind):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:                     # noqa: PERF203
            print(f"  raw unreadable: {path.name} ({exc})")
            stats["unreadable"] += 1
            continue
        stats["files"] += 1
        if isinstance(payload, dict):
            payload = payload.get("entries") or payload.get("events") or []
        for ev in payload or []:
            if not isinstance(ev, dict):
                continue
            stats["records"] += 1
            eid = ev.get("id") or ev.get("eventId")
            if not eid:
                stats["no_id"] += 1
                continue
            by_id[str(eid)] = ev
    return by_id, stats


# --------------------------------------------------------------------------
# reading the conformed table as it stands
# --------------------------------------------------------------------------

def existing_rows(table: str) -> tuple[list[dict], list[dict]]:
    """(real, synthetic) rows currently landed, across every partition."""
    real: list[dict] = []
    synth: list[dict] = []
    for path in table_day_partitions(table):
        try:
            rows = pq.read_table(path).to_pylist()
        except Exception as exc:                                 # noqa: BLE001
            print(f"  partition unreadable: {path} ({exc})")
            continue
        for r in rows:
            # A partition written before `is_synthetic` existed has no such
            # column and its rows are real — the same defaulting `read_table`
            # applies, restated here because this tool reads parquet directly.
            (synth if r.get("is_synthetic") else real).append(r)
    return real, synth


# --------------------------------------------------------------------------
# the audit — what actually changed, measured rather than asserted
# --------------------------------------------------------------------------

def quantiles(values: list[float], qs=(0.05, 0.25, 0.5, 0.75, 0.95)) -> dict:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {}
    out = {}
    for q in qs:
        i = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        out[q] = vals[i]
    return out


def _fmt_hours(h: float | None) -> str:
    if h is None:
        return "-"
    if h < 48:
        return f"{h:,.1f}h"
    return f"{h:,.0f}h ({h / 24:,.1f}d)"


def audit(kind: str, before: list[dict], after: list[dict]) -> None:
    print(f"\n  --- {kind}: what changed ---")

    def cov(rows, field):
        if not rows:
            return "-"
        n = sum(1 for r in rows if r.get(field) is not None)
        return f"{n:,}/{len(rows):,} ({n / len(rows):.1%})"

    fields = ["duration_hours", "port_id", "visit_port_id", "dwell_hours",
              "visit_confidence", "gfw_confidence_raw", "confidence"]
    print(f"    {'field':<24}{'before':>22}{'after':>22}")
    for f in fields:
        print(f"    {f:<24}{cov(before, f):>22}{cov(after, f):>22}")

    if kind != "port_visits":
        return

    print(f"\n    {'span vs dwell':<24}{'p05':>12}{'p25':>12}{'p50':>12}"
          f"{'p75':>12}{'p95':>12}")
    for label, rows, field in (("duration_hours (before)", before, "duration_hours"),
                               ("duration_hours (after)", after, "duration_hours"),
                               ("dwell_hours (after)", after, "dwell_hours")):
        q = quantiles([r.get(field) for r in rows])
        cells = "".join(f"{_fmt_hours(q.get(k)):>12}" for k in
                        (0.05, 0.25, 0.5, 0.75, 0.95))
        print(f"    {label:<24}{cells}")

    n = len(after) or 1
    stop = sum(1 for r in after if r.get("visit_has_stop"))
    agree = sum(1 for r in after if r.get("visit_anchorages_agree") is True)
    disagree = sum(1 for r in after if r.get("visit_anchorages_agree") is False)
    unknown = sum(1 for r in after if r.get("visit_anchorages_agree") is None)
    dwell = sum(1 for r in after if r.get("dwell_hours") is not None)
    print(f"\n    visit structure, {len(after):,} rows")
    print(f"      observed a stop            {stop:>7,} ({stop / n:.1%})")
    print(f"      entry and exit agree       {agree:>7,} ({agree / n:.1%})")
    print(f"      entry and exit differ      {disagree:>7,} ({disagree / n:.1%})"
          f"   <- span is not a dwell")
    print(f"      too few anchorages to tell {unknown:>7,} ({unknown / n:.1%})"
          f"   <- unknown, not zero")
    print(f"      dwell_hours populated      {dwell:>7,} ({dwell / n:.1%})")

    by_src: dict[str, int] = defaultdict(int)
    for r in after:
        by_src[r.get("visit_port_source") or "none"] += 1
    print("    port attributed from: "
          + ", ".join(f"{k} {v:,}" for k, v in sorted(by_src.items())))

    longest = sorted((r for r in after if r.get("duration_hours") is not None),
                     key=lambda r: -r["duration_hours"])[:5]
    if longest:
        print("\n    the five longest spans, and what each one is:")
        for r in longest:
            verdict = ("dwell" if r.get("dwell_hours") is not None
                       else "NOT a dwell — "
                            + ("no observed stop" if not r.get("visit_has_stop")
                               else "entry and exit anchorages differ"
                               if r.get("visit_anchorages_agree") is False
                               else "too few anchorages to tell"))
            print(f"      {_fmt_hours(r['duration_hours']):>18}  "
                  f"{str(r.get('ship_name') or r.get('vessel_id'))[:22]:<24}"
                  f"{verdict}")


# --------------------------------------------------------------------------
# rebuilding
# --------------------------------------------------------------------------

def _day_of(row: dict, field: str) -> str:
    v = row.get(field)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(v, str) and len(v) >= 10:
        return v[:10]
    return "unknown"


def rebuild_kind(kind: str, *, dry_run: bool) -> dict:
    table = EVENT_SPECS[kind]["table"]
    raw_by_id, raw_stats = raw_events(kind)
    real, synth = existing_rows(table)

    if not raw_by_id:
        return dict(kind=kind, table=table, note="no raw payloads on disk",
                    real=len(real), synthetic=len(synth))

    prior_ingested = {str(r.get("event_id")): r.get("ingested_at")
                      for r in real if r.get("event_id")}

    rebuilt: list[dict] = []
    dropped = 0
    for eid, ev in raw_by_id.items():
        row = map_event(ev, kind)
        if row is None:
            # Out of AOI, or missing an id or start. `map_event` already
            # refused these at first landing, so this count is expected to
            # match the connector's "skipped" line, not to be zero.
            dropped += 1
            continue
        # A re-derivation is not a new ingest. See the module docstring.
        if eid in prior_ingested and prior_ingested[eid] is not None:
            row["ingested_at"] = prior_ingested[eid]
        rebuilt.append(row)

    rebuilt_ids = {str(r["event_id"]) for r in rebuilt}
    orphans = [r for r in real if str(r.get("event_id")) not in rebuilt_ids]

    audit(kind, real, rebuilt)

    if not dry_run:
        _write(table, rebuilt + orphans + synth)

    return dict(kind=kind, table=table, raw_files=raw_stats["files"],
                raw_records=raw_stats["records"], raw_unique=len(raw_by_id),
                real_before=len(real), rebuilt=len(rebuilt), dropped=dropped,
                orphans=len(orphans), synthetic=len(synth))


def _write(table: str, rows: list[dict]) -> None:
    """Rewrite the table from a complete row set, partition by partition.

    Whole-table rather than merge-in-place, because a re-derivation can move a
    row between day partitions if its parsed start time differs, and a merge
    would leave the old copy behind under the old day — one event, two rows,
    both looking authoritative.
    """
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[_day_of(r, "start_time")].append(r)

    keep: set[Path] = set()
    for day, drows in by_day.items():
        if day == "unknown":
            print(f"  WARNING: {len(drows)} row(s) with no usable start_time; "
                  f"left out of the rewrite rather than guessed into a day")
            continue
        path = conformed_dir(table) / f"day={day}"
        path.mkdir(parents=True, exist_ok=True)
        path = path / "part.parquet"
        out = _normalise_for_arrow(drows)
        pq.write_table(pa.Table.from_pylist(out), path, compression="zstd")
        keep.add(path)

    for stale in table_day_partitions(table):
        if stale not in keep:
            # The partition's rows all moved elsewhere. Leaving the file would
            # duplicate every one of them on the next read.
            stale.unlink()
            print(f"  removed now-empty partition {stale.parent.name}")

    # A rebuilt column that is null across a whole day would otherwise be typed
    # `null` there and `double` elsewhere, which makes the table unreadable as a
    # set. See landing.reconcile_null_columns.
    fixed = reconcile_null_columns(table)
    if fixed:
        print(f"  retyped all-null columns in {fixed} partition(s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="audit and report, write nothing")
    ap.add_argument("--kind", choices=sorted(EVENT_SPECS),
                    help="one event kind; default is all four")
    args = ap.parse_args()

    kinds = [args.kind] if args.kind else list(EVENT_SPECS)
    print("Re-deriving conformed GFW tables from immutable raw (CLAUDE.md §4.2)")
    print("No network is used. Synthetic rows are carried through untouched.")
    if args.dry_run:
        print("DRY RUN: nothing will be written.")
    else:
        print("Writing. Back up data/conformed first if you have not.")

    results = [rebuild_kind(k, dry_run=args.dry_run) for k in kinds]

    print(f"\n  {'kind':<14}{'raw uniq':>10}{'real before':>13}"
          f"{'rebuilt':>10}{'dropped':>9}{'orphans':>9}{'synthetic':>11}")
    total_orphans = 0
    for r in results:
        if r.get("note"):
            print(f"  {r['kind']:<14}  ({r['note']}; "
                  f"{r['real']:,} real / {r['synthetic']:,} synthetic left as-is)")
            continue
        print(f"  {r['kind']:<14}{r['raw_unique']:>10,}{r['real_before']:>13,}"
              f"{r['rebuilt']:>10,}{r['dropped']:>9,}{r['orphans']:>9,}"
              f"{r['synthetic']:>11,}")
        total_orphans += r["orphans"]

    print()
    if total_orphans:
        print(f"WARNING: {total_orphans:,} landed real row(s) have no matching "
              f"raw record. They were preserved, not deleted — but raw is "
              f"supposed to be sufficient to regenerate every one of them, so "
              f"this number being non-zero means part of raw is missing and "
              f"'derived data is reproducible' is currently not true for this "
              f"corpus. Worth finding before it matters.")
    if args.dry_run:
        print("Re-run without --dry-run to apply.")
    else:
        print("Done. Re-run tools/corpus_profile.py to refresh the profile, "
              "then tools/restamp_h3.py --dry-run to confirm the rewritten "
              "partitions still carry all five H3 resolutions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
