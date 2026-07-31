"""Read the real OFAC snapshot out of DuckDB, and say so when it cannot.

Split into its own module so the scenario collision guard and the corpus
profiler share one implementation, and so neither has to import the matcher
wholesale.

**Why this file has a diagnostic instead of a bare `set()`.** The first version
swallowed every exception and returned an empty set, so the profiler printed
`ofac_sdn (duckdb)  0 vessel IMO(s)` — which reads as a measurement ("OFAC lists
no vessels") when it actually meant "something went wrong and I am not telling
you what." Measured on the operator's corpus that line printed 0 while the
registry connector had landed **1,516 vessels**, and the collision guard
therefore ran against a denominator of 121 while reporting success.

That is the failure family this project keeps hitting: a check that cannot
distinguish "the answer is zero" from "the check did not run" (STATE.md — the
green `doctor` that masked three separate faults). So every failure path here
returns a reason, and the caller prints it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Table names the OFAC snapshot has been written under. `registries.py` writes
#: `ofac_sdn`; the others are tried because a silent miss on a renamed table is
#: exactly the failure this module exists to make loud.
CANDIDATE_TABLES = ("ofac_sdn", "sdn", "ofac", "sanctions_ofac", "ofac_entries")

#: Column holding the free-text block OFAC hides IMO numbers in.
REMARKS_COLUMNS = ("remarks", "remark", "notes", "comments", "text")

#: Column naming the kind of entry, so vessels can be separated from people.
TYPE_COLUMNS = ("sdn_type", "sdntype", "type", "entry_type", "entity_type")


@dataclass
class OfacLookup:
    """IMOs plus the story of how they were (or were not) obtained."""
    imos: set[str] = field(default_factory=set)
    ok: bool = False
    reason: str = ""
    table: str = ""
    n_rows: int = 0

    def describe(self) -> str:
        if self.ok:
            return (f"{len(self.imos):,} vessel IMO(s) from {self.table} "
                    f"({self.n_rows:,} row(s) scanned)")
        return f"UNAVAILABLE — {self.reason}"


def _tables(con) -> list[str]:
    try:
        return [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    except Exception as exc:                                   # noqa: BLE001
        raise RuntimeError(f"cannot list tables ({exc})") from exc


def _columns(con, table: str) -> list[str]:
    try:
        return [d[0] for d in con.execute(
            f'SELECT * FROM "{table}" LIMIT 0').description]
    except Exception:                                          # noqa: BLE001
        return []


def ofac_snapshot() -> OfacLookup:
    """Every IMO in the real OFAC snapshot, with a reason when there are none.

    **OFAC does not live in a conformed table.** `registries.py` writes it to
    DuckDB, and there is no `sanctions` parquet table at all — confirmed against
    the operator's corpus, where that directory does not exist. Reading
    `read_table("sanctions")` returned nothing and the collision guard silently
    checked against a far smaller denominator than it claimed to.

    OFAC has no IMO column; the number is regexed out of the free-text remarks
    field, which is the same extraction `sanctions_match` performs. Reusing
    `normalise_imo` means the guard sees exactly the hulls the matcher would.
    """
    try:
        from ..db import connect
        from .sanctions_match import normalise_imo
    except Exception as exc:                                   # noqa: BLE001
        return OfacLookup(reason=f"cannot import the DuckDB layer ({exc})")

    try:
        con = connect(read_only=True)
    except Exception as exc:                                   # noqa: BLE001
        return OfacLookup(reason=f"cannot open the DuckDB database ({exc}). "
                                 f"Has `ingest registries` run on this machine?")

    try:
        try:
            names = _tables(con)
        except RuntimeError as exc:
            return OfacLookup(reason=str(exc))

        table = next((t for t in CANDIDATE_TABLES if t in names), "")
        if not table:
            near = [t for t in names if "ofac" in t.lower() or "sdn" in t.lower()]
            return OfacLookup(reason=(
                f"no OFAC table found. Tried {', '.join(CANDIDATE_TABLES)}; "
                f"the database holds {len(names)} table(s)"
                + (f" including {', '.join(near)} — add it to CANDIDATE_TABLES"
                   if near else f": {', '.join(sorted(names)[:12])}")))

        cols = {c.lower(): c for c in _columns(con, table)}
        remarks_col = next((cols[c] for c in REMARKS_COLUMNS if c in cols), "")
        if not remarks_col:
            return OfacLookup(table=table, reason=(
                f"{table} has no remarks-like column — IMO numbers live in free "
                f"text and there is nowhere to read them from. Columns: "
                f"{', '.join(sorted(cols.values())[:15])}"))

        type_col = next((cols[c] for c in TYPE_COLUMNS if c in cols), "")
        # Filtering to vessels is an optimisation, not a requirement: a person's
        # remarks will not contain a valid IMO check digit. Falling back to the
        # whole table when the type column is named something unexpected is far
        # better than returning zero and calling it a measurement.
        where = (f"WHERE lower(coalesce(\"{type_col}\", '')) LIKE '%vessel%'"
                 if type_col else "")
        try:
            rows = con.execute(
                f'SELECT "{remarks_col}" FROM "{table}" {where}').fetchall()
        except Exception as exc:                               # noqa: BLE001
            return OfacLookup(table=table,
                              reason=f"query against {table} failed ({exc})")
    finally:
        try:
            con.close()
        except Exception:                                      # noqa: BLE001
            pass

    out: set[str] = set()
    for (remarks,) in rows:
        for m in re.findall(r"\b(\d{7})\b", str(remarks or "")):
            v = normalise_imo(m)
            if v:
                out.add(str(v))

    if not rows:
        return OfacLookup(table=table, n_rows=0, reason=(
            f"{table} matched 0 row(s)"
            + (f" with {type_col} like '%vessel%' — the type column may use "
               f"different wording" if type_col
               else " — the table is empty")))
    return OfacLookup(imos=out, ok=True, table=table, n_rows=len(rows))


def ofac_imos_from_duckdb() -> set[str]:
    """Backwards-compatible shim. Prefer `ofac_snapshot`, which explains itself."""
    return ofac_snapshot().imos
