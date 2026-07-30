"""Two checks that turn a recurring reporting bug into something structural.

**The bug class.** Three times in one session a green number was reported over
unverified reality: a passing `doctor` over a token that could not be sent, a
populated provenance envelope over a `confidence` column that was entirely
null, and "landed 173 matches" when 127 rows reached disk. Nothing was faked —
each number was simply measured one step earlier than the claim it supported.

That is not a lesson to remember, it is a check to run. Two of them:

1. `report_landed()` — a row count may only be announced after the write
   returns it. `land_table` merges on the natural key, so rows built and rows
   landed differ whenever one record arrives from several sources. The built
   count is never the answer, and no connector should be constructing that
   sentence itself.

2. `check_coverage()` — a column existing is not a column having values. Each
   table declares the fields it should carry; a field whose floor is a number
   fails below it, and a field whose floor is `None` is **reported but not
   gated** — the honest state for anything we have not yet measured on live
   data. Converting a `None` into a number is a deliberate act performed
   against a real run, which is the point: a floor invented in a sandbox would
   be the same class of error this module exists to stop.

Neither check knows anything about a specific source, so nothing here belongs
in a connector (CLAUDE.md §4.5).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

#: Floors derived from the code path, not from a sample. Each of these is set
#: by construction — the mapper cannot emit a row without it — so anything less
#: than 100% is a real defect rather than thin data.
#:
#: Everything else is `None`: reported on every ingest report, gated by nothing,
#: until a live run on the deploy laptop supplies a measured rate. See §5 of
#: CLAUDE.md — "built to do" is not "currently doing", and that applies to our
#: own quality floors too.
_BY_CONSTRUCTION = 1.0

COVERAGE_EXPECTATIONS: dict[str, dict[str, float | None]] = {
    # --- GFW events -------------------------------------------------------
    # map_event() returns None unless event_id and start_time both parse, so
    # those two are guaranteed. lat/lon are NOT: a GAP event can arrive without
    # a position and is still landed, which is why the H3 columns cannot be
    # floored either — stamp_h3 skips positionless rows by design.
    "gfw_encounters": {
        "event_id": _BY_CONSTRUCTION, "start_time": _BY_CONSTRUCTION,
        "lat": None, "lon": None, "h3_r6": None, "h3_r7": None,
        "vessel_id": None, "counterpart_vessel_id": None, "duration_hours": None,
    },
    "gfw_loitering": {
        "event_id": _BY_CONSTRUCTION, "start_time": _BY_CONSTRUCTION,
        "lat": None, "lon": None, "h3_r6": None, "h3_r7": None,
        "vessel_id": None, "duration_hours": None,
    },
    "gfw_port_visits": {
        "event_id": _BY_CONSTRUCTION, "start_time": _BY_CONSTRUCTION,
        "lat": None, "lon": None, "h3_r6": None, "h3_r7": None,
        "vessel_id": None, "port_id": None, "port_name": None,
    },
    "gfw_ais_gaps": {
        "event_id": _BY_CONSTRUCTION, "start_time": _BY_CONSTRUCTION,
        "lat": None, "lon": None, "h3_r6": None, "h3_r7": None,
        "vessel_id": None, "gap_duration_hours": None,
        # GFW only marks intentional disabling on gaps it has assessed. A low
        # rate here is GFW's caution, not our mapping — and the flag is the
        # input to the whole identity-change-then-anomaly analytic, so its rate
        # is worth watching even though it must never gate.
        "gfw_intentional_disabling": None,
    },
    # --- GFW identity -----------------------------------------------------
    "gfw_vessel_identity": {
        "vessel_id": _BY_CONSTRUCTION, "record_kind": _BY_CONSTRUCTION,
        "ship_name": None, "mmsi": None, "imo": None, "flag": None,
        "valid_from": None, "valid_to": None,
    },
    "gfw_vessel_owners": {
        "vessel_id": _BY_CONSTRUCTION,
        # Measured at 0.66% of vessels having any owner record at all — the
        # negative finding behind ADR-016a. That is a rate across vessels, not
        # across rows in this table, so it is not a floor here.
        "owner_name": None, "owner_flag": None,
    },
    # --- sanctions --------------------------------------------------------
    # Every field here is written unconditionally by sanctions_match.run(), so
    # every one is floored. This is the table where an all-null column would do
    # the most damage: a match row with no tier or no confidence is exactly the
    # untraceable assertion CLAUDE.md §4.1 forbids.
    "sanctioned_vessel_matches": {
        "vessel_id": _BY_CONSTRUCTION, "ofac_ent_num": _BY_CONSTRUCTION,
        "match_tier": _BY_CONSTRUCTION, "is_finding": _BY_CONSTRUCTION,
        "confidence": _BY_CONSTRUCTION, "ofac_name": _BY_CONSTRUCTION,
        "sanctions_as_of": _BY_CONSTRUCTION,
        "ofac_imo": None, "ofac_program": None, "ofac_owner": None,
        "ship_name": None, "flag": None,
    },
}

#: Fields every landed row carries regardless of table (CLAUDE.md §4.1).
#: `confidence` is explicitly nullable there, so it is absent — but a table that
#: *does* assert confidence should floor it in COVERAGE_EXPECTATIONS, which is
#: what would have caught the all-null confidence column.
ENVELOPE_FIELDS = ("source_id", "source_ref", "acquired_at", "ingested_at",
                   "pipeline_version")


def landed(written: Mapping[str, int]) -> int:
    """Sum what `land_table` actually wrote.

    Trivial on purpose. It exists so connectors have something to call that is
    visibly not `len(rows)`, and so a reviewer has one name to grep for.
    """
    return sum(written.values())


def report_landed(tag: str, table: str, written: Mapping[str, int],
                  built: int, *, noun: str = "row") -> str:
    """The one sanctioned way to announce a landing. Returns what it printed.

    The landed count comes first and the built count appears only as the
    explanation for a gap. That order is the whole point: the number a reader
    carries away should be the one that is on disk.
    """
    n = landed(written)
    line = f"[{tag}] landed {n:,} {noun}(s) into {table} " \
           f"({len(written)} day partition(s))"
    if built != n:
        line += (f"\n[{tag}]   {built:,} built, {built - n:,} merged onto rows already "
                 "there by natural key — landed is the count that is real")
    print(line)
    return line


def coverage(rows: Sequence[Mapping], fields: Sequence[str]) -> dict[str, float]:
    """Non-null rate per field. Empty string counts as null; 0 and False do not.

    A field absent from every row scores 0.0 rather than raising — a missing
    column and an all-null column read identically to anyone downstream, and
    should fail identically here.
    """
    n = len(rows)
    if not n:
        return {f: 0.0 for f in fields}
    out = {}
    for f in fields:
        filled = sum(1 for r in rows if r.get(f) is not None and r.get(f) != "")
        out[f] = filled / n
    return out


def check_coverage(table: str, rows: Sequence[Mapping],
                   expectations: Mapping[str, Mapping[str, float | None]] | None = None
                   ) -> list[str]:
    """Return one message per field below its floor. Empty list = passing.

    Fields with a `None` floor are never returned here — they are observed, not
    gated. Use `coverage_report` to see them.

    Returns rather than raises: the report tool wants every failure at once,
    and a connector may prefer to land the rows and complain loudly rather than
    discard a download that took an hour.
    """
    exp = (expectations or COVERAGE_EXPECTATIONS).get(table)
    if not exp:
        return []
    floored = {f: v for f, v in exp.items() if v is not None}
    if not floored:
        return []
    got = coverage(rows, list(floored))
    return [
        f"{table}.{f}: {got[f]:.1%} non-null, expected >= {floor:.0%}"
        for f, floor in floored.items() if got[f] < floor
    ]


def coverage_report(table: str, rows: Sequence[Mapping],
                    expectations: Mapping[str, Mapping[str, float | None]] | None = None
                    ) -> list[tuple[str, float, float | None]]:
    """(field, observed_rate, floor_or_None) for every declared field.

    Sorted rarest-first, because the fields worth looking at are the empty ones.
    """
    exp = (expectations or COVERAGE_EXPECTATIONS).get(table)
    if not exp:
        return []
    got = coverage(rows, list(exp))
    return sorted(((f, got[f], exp[f]) for f in exp), key=lambda t: t[1])


def tables_with_expectations() -> list[str]:
    return sorted(COVERAGE_EXPECTATIONS)
