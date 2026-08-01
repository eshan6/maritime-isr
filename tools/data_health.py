"""What in the landed data would embarrass a live demo. Read-only.

    python tools/data_health.py

Reads the conformed tables and the raw store. Writes nothing, uses no network.
Exits **1** if any BLOCKER is present, **0** otherwise, so it can gate a demo
rather than merely inform one.

**Why this is a tool and not a checklist.** Every data defect this project has
hit was found by accident, late, while looking at something else: the H3
resolutions (ADR-015), the two vessel keyspaces, the port-visit durations
(ADR-020). Each was invisible in row counts and obvious the moment somebody
printed the right number. A demo is the worst possible place to discover the
next one, because the failure is silent — a map that draws, a panel that fills,
and a claim underneath it that is wrong.

So this asks, of the data as it actually sits on disk, the questions whose wrong
answers would show up in front of an operator:

  * **BLOCKER** — the demo would state something false, or a core query returns
    nothing. Fix before demoing.
  * **WARN** — the demo works but a number on screen is weaker than it looks.
    Know about it so you are not surprised by a question.
  * **INFO** — measured context, no action.

Nothing here is graded on whether the codebase *intends* to handle a case. It
grades what is on disk. An existence check ("the column is there") is worth
nothing; every check below reads values.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.config import cfg                              # noqa: E402
from maritime_isr.h3util import RESOLUTIONS                      # noqa: E402
from maritime_isr.ingest.landing import read_table               # noqa: E402

BLOCKER, WARN, INFO = "BLOCKER", "WARN", "INFO"
_WINDOW_RE = re.compile(r"_(\d{8})_(\d{8})\.json$")

EVENT_TABLES = ("gfw_encounters", "gfw_loitering", "gfw_port_visits",
                "gfw_ais_gaps")


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, level: str, check: str, finding: str, action: str = "") -> None:
        self.rows.append((level, check, finding, action))

    @property
    def blockers(self) -> int:
        return sum(1 for r in self.rows if r[0] == BLOCKER)

    def render(self) -> str:
        order = {BLOCKER: 0, WARN: 1, INFO: 2}
        out = []
        for level, check, finding, action in sorted(
                self.rows, key=lambda r: (order[r[0]], r[1])):
            out.append(f"  [{level:<7}] {check}")
            out.append(f"            {finding}")
            if action:
                out.append(f"            -> {action}")
        return "\n".join(out)


def _as_dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and len(v) >= 10:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _real(table: str) -> list[dict]:
    try:
        return [r for r in read_table(table) if not r.get("is_synthetic")]
    except Exception:                                          # noqa: BLE001
        return []


# --------------------------------------------------------------------------

def check_tables_readable(rep: Report) -> dict[str, list[dict]]:
    """Every table opens, and opens *together*.

    Reading one partition proves nothing: the failure mode that matters is a
    table whose partitions disagree on a column's type, which reads fine one
    file at a time and fails as a set. That is exactly what an all-null column
    produces (see `landing.reconcile_null_columns`), and it takes down every
    query in the product at once.
    """
    tables: dict[str, list[dict]] = {}
    for t in EVENT_TABLES + ("gfw_vessel_identity", "sanctioned_vessel_matches"):
        try:
            tables[t] = [r for r in read_table(t) if not r.get("is_synthetic")]
        except Exception as exc:                               # noqa: BLE001
            tables[t] = []
            rep.add(BLOCKER, f"{t}: unreadable",
                    f"{type(exc).__name__}: {exc}",
                    "if this mentions a cast to NULL, one day partition has an "
                    "all-null column; land any row or run "
                    "tools/rebuild_conformed.py to retype it")
    return tables


def check_h3(rep: Report, tables: dict) -> None:
    """All five resolutions, present on rows that have a position.

    Without res 6 the ingest tables and the fusion tables cannot be joined at
    all, so the demo's central question — which ship is this contact — returns
    nothing while every table looks full.
    """
    for t, rows in tables.items():
        positioned = [r for r in rows if r.get("lat") is not None]
        if not positioned:
            continue
        missing = {f"h3_r{res}": sum(1 for r in positioned
                                     if not r.get(f"h3_r{res}"))
                   for res in RESOLUTIONS}
        bad = {k: v for k, v in missing.items() if v}
        if bad:
            rep.add(BLOCKER, f"{t}: H3 coverage",
                    f"{len(positioned):,} positioned row(s), missing "
                    + ", ".join(f"{k} on {v:,}" for k, v in bad.items()),
                    "python tools/restamp_h3.py")


def check_provenance(rep: Report, tables: dict) -> None:
    """The envelope, on every row. A flag with no traceable source is a landmine."""
    need = ("source_id", "source_ref", "acquired_at", "ingested_at",
            "pipeline_version")
    for t, rows in tables.items():
        if not rows:
            continue
        gaps = {c: sum(1 for r in rows if r.get(c) is None) for c in need}
        bad = {k: v for k, v in gaps.items() if v}
        if bad:
            rep.add(BLOCKER, f"{t}: provenance envelope",
                    f"{len(rows):,} row(s), missing "
                    + ", ".join(f"{k} on {v:,}" for k, v in bad.items()),
                    "CLAUDE.md §4.1 — a row that cannot be traced cannot be "
                    "shown to an analyst")


def check_result_caps(rep: Report, tables: dict) -> None:
    """A round row count is a page limit, not a measurement.

    This matters for a demo because it changes what a count *means*. "3,000 port
    visits in the Arabian Sea" is a statement about GFW's response size, not
    about the sea, and an operator will reasonably hear the second one.
    """
    for t, rows in tables.items():
        n = len(rows)
        if n and n % 1000 == 0 and n >= 1000:
            rep.add(WARN, f"{t}: possible result cap",
                    f"exactly {n:,} rows — a round number nobody chose is "
                    f"usually an API page limit",
                    "do not quote this as a count of what exists in the AOI; "
                    "say 'the first N returned'")


def check_port_visit_spans(rep: Report, rows: list[dict]) -> None:
    """Multi-year port visits, and what they do to a graph.

    A `docked-at` edge running from 2012 to 2026 is 'current' for the whole
    demo window, so port nodes become hubs on the strength of a handful of rows
    and any 'who was here' answer is swamped. Whether those spans are genuine
    long stays over-sampled by an overlap query, or something else, is open
    (ADR-020) — but either way an edge asserting a precise fourteen-year
    presence is asserting precision the data does not have.
    """
    if not rows:
        return
    spans = [(r.get("duration_hours") or 0.0) for r in rows]
    long_ = [h for h in spans if h > 24 * 60]        # over two months
    huge = [h for h in spans if h > 24 * 365]        # over a year
    if huge:
        rep.add(WARN, "gfw_port_visits: multi-year spans",
                f"{len(huge):,} of {len(rows):,} visits ({len(huge)/len(rows):.1%}) "
                f"span more than a year; longest {max(huge)/24:,.0f} days",
                "do not render these as 'time alongside'. ADR-020 is open; "
                "run tools/port_visit_forensics.py")
    elif long_:
        rep.add(INFO, "gfw_port_visits: long spans",
                f"{len(long_):,} visits span more than two months")

    no_conf = sum(1 for r in rows if r.get("confidence") is None)
    if no_conf == len(rows):
        rep.add(WARN, "gfw_port_visits: no confidence",
                f"all {len(rows):,} rows carry no confidence at all — GFW's own "
                f"judgement of how much of each visit they saw was dropped",
                "python tools/rebuild_conformed.py (re-derives from raw, no "
                "network)")
    elif no_conf:
        rep.add(INFO, "gfw_port_visits: confidence",
                f"{len(rows) - no_conf:,} of {len(rows):,} rows carry GFW "
                f"confidence")


def check_length_bias(rep: Report, rows: list[dict]) -> None:
    """Is any duration quoted from an overlap query de-biased?

    An events query asks for everything *overlapping* a window, which
    over-samples long events in proportion to their length. Any duration
    statistic taken over the whole table inherits that, and it is not a small
    effect here.
    """
    root = cfg.data_root / "raw" / "gfw-events"
    lo = hi = None
    if root.exists():
        for p in root.glob("day=*/*.json"):
            m = _WINDOW_RE.search(p.name)
            if not m:
                continue
            a = datetime.strptime(m.group(1), "%Y%m%d").replace(
                tzinfo=timezone.utc)
            b = datetime.strptime(m.group(2), "%Y%m%d").replace(
                tzinfo=timezone.utc)
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    if not (lo and hi and rows):
        return

    inside = [r for r in rows
              if (_as_dt(r.get("start_time")) or lo - timedelta(1)) >= lo
              and (_as_dt(r.get("end_time")) or hi + timedelta(1)) <= hi]
    frac = len(inside) / len(rows)
    if frac < 0.9:
        rep.add(INFO, "gfw_port_visits: length bias",
                f"{len(inside):,} of {len(rows):,} visits ({frac:.1%}) began "
                f"and ended inside the {(hi - lo).days}-day query window; the "
                f"other {1 - frac:.0%} cross an edge and are over-represented "
                f"in proportion to their length",
                "quote duration statistics from the contained subset only — "
                "tools/corpus_profile.py now does")


def check_raw_completeness(rep: Report) -> None:
    """Can the conformed layer actually be regenerated?

    CLAUDE.md §4.2 says every derived output is regenerable from raw plus a git
    SHA. That is the project's only defence against silent corruption, and it is
    a claim, not a fact, until something checks it.
    """
    root = cfg.data_root / "raw"
    if not root.exists():
        rep.add(BLOCKER, "raw store missing",
                f"{root} does not exist — nothing downstream is reproducible "
                f"and no mapper fix can be applied to already-landed rows",
                "CLAUDE.md §4.2. Without raw, a re-download is the only repair, "
                "which changes the corpus window (ADR-013)")
        return
    n = sum(1 for _ in root.rglob("*") if _.is_file())
    rep.add(INFO, "raw store",
            f"{n:,} file(s) under {root} — conformed tables can be re-derived "
            f"with tools/rebuild_conformed.py")


def check_synthetic_separation(rep: Report) -> None:
    """Real and synthetic must be distinguishable and never blended."""
    for t in EVENT_TABLES + ("gfw_vessel_identity", "ais_position"):
        try:
            rows = read_table(t)
        except Exception:                                      # noqa: BLE001
            continue
        if not rows:
            continue
        syn = [r for r in rows if r.get("is_synthetic")]
        bad = [r for r in rows
               if bool(r.get("is_synthetic"))
               != (r.get("source_id") == "synthetic-scenario")]
        if bad:
            rep.add(BLOCKER, f"{t}: flag/source disagreement",
                    f"{len(bad):,} row(s) where is_synthetic and source_id "
                    f"disagree — every real-vs-synthetic split is silently wrong",
                    "ADR-019. These cannot be produced by stamp_envelope, so "
                    "something wrote them directly")
        if syn:
            rep.add(INFO, f"{t}: composition",
                    f"{len(rows) - len(syn):,} real, {len(syn):,} synthetic")


def check_demo_has_something_to_show(rep: Report, tables: dict) -> None:
    """The blunt one: is there anything in here to demo at all?"""
    gaps = tables.get("gfw_ais_gaps", [])
    enc = tables.get("gfw_encounters", [])
    flagged = [r for r in gaps if r.get("gfw_intentional_disabling")]
    # **Always reported, both ways.** A check that only speaks when the answer
    # is zero makes the non-zero case invisible — the count silently vanished
    # from this report the first time it went above zero, which is precisely
    # the number a demo lives or dies on.
    if not flagged:
        rep.add(WARN, "no flagged dark-vessel gaps",
                f"{len(gaps):,} AIS gap(s) landed, 0 flagged by GFW as "
                f"intentional disabling",
                "the demo cannot show a real dark vessel from this corpus. "
                "Run the scenario corpus alongside it and label every figure "
                "as synthetic (ADR-019), or widen the pull")
    else:
        rep.add(INFO, "flagged dark-vessel gaps",
                f"{len(flagged):,} of {len(gaps):,} AIS gap(s) are flagged by "
                f"GFW as intentional disabling — vessel(s) "
                + ", ".join(sorted({str(r.get("vessel_id"))[:12]
                                    for r in flagged})[:5]),
                "this is GFW's finding, not ours (CLAUDE.md §6) — quote it as "
                "'GFW assessed this gap as intentional', never as our own "
                "dark-vessel detection")
    if len(enc) < 20:
        rep.add(WARN, "encounter graph is thin",
                f"{len(enc):,} encounter(s) across the whole AOI and window",
                "a network view has almost nothing to draw; prefer a ranked "
                "table for the demo")


def main() -> int:
    rep = Report()
    print("data health — what would embarrass a demo (read-only)\n")

    tables = check_tables_readable(rep)
    check_h3(rep, tables)
    check_provenance(rep, tables)
    check_result_caps(rep, tables)
    check_raw_completeness(rep)
    check_synthetic_separation(rep)
    check_demo_has_something_to_show(rep, tables)

    pv = tables.get("gfw_port_visits", [])
    check_port_visit_spans(rep, pv)
    check_length_bias(rep, pv)

    print(rep.render() or "  nothing to report — which is itself worth a "
                          "second look, because no check found any data")
    counts = Counter(r[0] for r in rep.rows)
    print(f"\n  {counts[BLOCKER]} blocker(s), {counts[WARN]} warning(s), "
          f"{counts[INFO]} note(s)")
    if rep.blockers:
        print("\n  NOT DEMO-READY. A blocker means the demo would either fail "
              "outright or state something false on screen.")
        return 1
    print("\n  No blockers. Warnings are things to know before an operator "
          "asks, not reasons to stop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
