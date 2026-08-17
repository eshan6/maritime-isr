"""The reported-vs-landed check pattern, and field coverage.

Three times in one session a green number was reported over unverified reality:
a passing doctor over an unsendable token, a populated provenance envelope over
an all-null confidence column, and "landed 173" when 127 rows reached disk.

These tests exist so the fourth instance fails in CI instead of in a report.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from maritime_isr.ingest import checks

REPO = pathlib.Path(__file__).resolve().parent.parent
INGEST = REPO / "maritime_isr" / "ingest"


# ==========================================================================
# 1. never report a built count as a landed count
# ==========================================================================

def test_landed_sums_the_write_result():
    assert checks.landed({"day=2026-06-14": 3, "day=2026-06-15": 4}) == 7
    assert checks.landed({}) == 0


def test_report_landed_leads_with_the_landed_count(capsys):
    checks.report_landed("tag", "some_table", {"day=2026-06-14": 5}, built=5)
    out = capsys.readouterr().out
    assert "landed 5 row(s) into some_table" in out


def test_report_landed_explains_a_gap_without_headlining_the_built_count(capsys):
    checks.report_landed("tag", "some_table", {"day=2026-06-14": 127}, built=173)
    out = capsys.readouterr().out
    assert "landed 127" in out, "the landed count is the headline"
    assert "landed 173" not in out, "the built count must never be the headline"
    assert "173 built" in out and "46 collapsed" in out, "the gap must be explained"


def test_report_landed_returns_what_it_printed():
    line = checks.report_landed("tag", "t", {"d": 1}, built=1)
    assert "landed 1" in line


def test_report_landed_never_prints_a_negative_count(capsys):
    """`5 built, -63 merged` reached an operator's terminal.

    It came from `land_table` returning the partition's size after the merge
    while `report_landed` subtracted that from the built count. Any arithmetic
    over a count that can go negative is arithmetic over the wrong number, so
    this asserts on the shape rather than on the one phrasing that broke.
    """
    checks.report_landed("zones", "maritime_zone", {"day=2026-08-17": 5}, built=5)
    out = capsys.readouterr().out
    assert not re.search(r"-\d", out), f"negative count in operator output: {out!r}"


#: Modules that may legitimately print a count that is not a land_table return.
#: `checks.py` defines the helper; `noaa_ais.py` is PARKED and already sums the
#: write result inline.
_REPORT_SCAN_SKIP = {"checks.py", "__init__.py"}

#: A print of the form `landed {len(...)}` or `landed {len(rows)} ...`. This is
#: the exact shape of the bug: a count taken from the list in memory rather
#: than from the write.
_BUILT_COUNT_REPORT = re.compile(r"landed\s*\{len\(")


def test_no_connector_prints_a_built_count_as_landed():
    """A grep-shaped guard, because the bug is grep-shaped.

    It cannot catch every phrasing, and it is not trying to. It catches the one
    that has actually happened, in the one place it has actually happened, and
    fails loudly enough that the next person reaches for `report_landed`.
    """
    offenders = []
    for p in sorted(INGEST.glob("*.py")):
        if p.name in _REPORT_SCAN_SKIP:
            continue
        src = p.read_text(encoding="utf-8")
        if _BUILT_COUNT_REPORT.search(src):
            offenders.append(p.name)
    assert not offenders, (
        f"these modules report a built count as landed: {offenders}. "
        "Use checks.report_landed(tag, table, written, built) — it takes the "
        "land_table return value, which is the count that is real."
    )


def test_every_module_that_lands_a_table_reports_through_the_helper():
    """Landing without announcing it honestly is the same defect, quieter."""
    missing = []
    for p in sorted(INGEST.glob("*.py")):
        if p.name in _REPORT_SCAN_SKIP or p.name == "landing.py":
            continue
        src = p.read_text(encoding="utf-8")
        if "land_table(" not in src:
            continue
        if "report_landed" not in src and "sum(w.values())" not in src \
                and "sum(written.values())" not in src:
            missing.append(p.name)
    assert not missing, (
        f"these modules land tables but never report a landed count: {missing}"
    )


# ==========================================================================
# 1b. what a landing reports is what THIS call put on disk
# ==========================================================================

def _rows(prefix: str, n: int, day: str = "2026-08-17"):
    from maritime_isr.ingest.landing import stamp_envelope
    from datetime import datetime, timezone
    at = datetime.fromisoformat(day).replace(hour=12, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        r = {"zone_id": f"{prefix}:{i}", "name": f"{prefix} {i}"}
        stamp_envelope(r, source_id=prefix, source_ref="v1", acquired_at=at,
                       confidence=0.9, is_synthetic=False)
        out.append(r)
    return out


@pytest.fixture
def _isolated(tmp_path, monkeypatch):
    from maritime_isr.config import cfg
    from maritime_isr.ingest.landing import conformed_dir
    monkeypatch.setattr(cfg, "data_root", tmp_path)
    assert tmp_path in conformed_dir("probe").parents, "redirect did not take"
    return tmp_path


def test_a_landing_counts_its_own_rows_not_the_partitions(_isolated, capsys):
    """The defect an operator hit, reproduced exactly.

    Two producers write into one day partition: `zones build` derives 63 zones,
    then `ingest zones` lands 5 territorial seas from a downloaded shapefile.
    The import announced `landed 68` — it claimed the other producer's 63 rows
    as its own — and then reported `5 built, -63 merged`.
    """
    from maritime_isr.ingest.landing import land_table

    land_table(_rows("derived", 63), table="t", key_fields=("zone_id",))
    written = land_table(_rows("imported", 5), table="t", key_fields=("zone_id",))

    assert checks.landed(written) == 5, (
        "the import landed 5 rows; anything else is another producer's work "
        "being counted as this one's")
    assert sum(written.partition_rows.values()) == 68, (
        "the partition total is still available — it is just not the headline")

    checks.report_landed("zones", "t", written, built=5, noun="zone")
    out = capsys.readouterr().out
    assert "landed 5 zone(s)" in out
    assert "68" not in out.split("now holds")[0], "68 must not read as landed"


def test_a_converged_rerun_says_so_instead_of_repeating_itself(_isolated, capsys):
    """Running the import twice printed byte-identical output.

    From the outside that is indistinguishable from a stuck command, and the
    operator read it as a loop. An idempotent connector has to say that the
    second run changed nothing, or its correctness looks like a hang.
    """
    from maritime_isr.ingest.landing import land_table

    first = land_table(_rows("imported", 5), table="t", key_fields=("zone_id",))
    assert sum(first.replaced.values()) == 0, "nothing was on disk to replace"

    second = land_table(_rows("imported", 5), table="t", key_fields=("zone_id",))
    assert checks.landed(second) == 5
    assert sum(second.replaced.values()) == 5, "every row replaced its own key"

    checks.report_landed("zones", "t", second, built=5, noun="zone")
    out = capsys.readouterr().out
    assert "the re-run converged" in out, (
        f"a re-run must announce that it changed nothing; got {out!r}")

    from maritime_isr.ingest.landing import read_table
    assert len(read_table("t")) == 5, "and it must not have duplicated"


def test_a_partly_new_batch_separates_the_new_rows_from_the_replaced_ones(
        _isolated, capsys):
    from maritime_isr.ingest.landing import land_table

    land_table(_rows("imported", 3), table="t", key_fields=("zone_id",))
    written = land_table(_rows("imported", 5), table="t", key_fields=("zone_id",))
    assert checks.landed(written) == 5
    assert sum(written.replaced.values()) == 3

    checks.report_landed("zones", "t", written, built=5, noun="zone")
    out = capsys.readouterr().out
    assert "3 of those replaced a row already on disk, 2 new" in out, out


def test_the_ais_writer_reports_its_own_rows_too(_isolated):
    """`writer.py` carried the same defect independently of `land_table`.

    `noaa_ais` prints `landed {sum(w.values())} AOI rows` straight from this
    return, so leaving one writer honest and the other not would put the bug
    back in an operator's terminal by a different route.
    """
    from datetime import datetime, timezone

    from maritime_isr.writer import write_position_reports

    ts = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)
    base = {"lat": 15.0, "lon": 68.0, "sog": 12.0, "timestamp": ts,
            "source_id": "t", "source_ref": "r", "acquired_at": ts,
            "ingested_at": ts, "pipeline_version": "abc", "confidence": None}

    w1 = write_position_reports([dict(base, mmsi=419000001)])
    assert sum(w1.values()) == 1

    # A second vessel in the same hour: this call landed one row, and the
    # partition now holds two. The report must say one.
    w2 = write_position_reports([dict(base, mmsi=419000002)])
    assert sum(w2.values()) == 1, (
        "the second call landed 1 row; reporting 2 counts the first call's row")


# ==========================================================================
# 2. a column existing is not a column having values
# ==========================================================================

def test_coverage_counts_nulls_and_empty_strings_as_missing():
    rows = [{"a": 1}, {"a": None}, {"a": ""}, {"a": 4}]
    assert checks.coverage(rows, ["a"])["a"] == 0.5


def test_coverage_does_not_treat_zero_or_false_as_missing():
    """`0` knots and `False` for intentional-disabling are real values.

    Counting them as null would make a correctly-populated column look empty
    and send someone hunting a mapping bug that does not exist.
    """
    rows = [{"speed": 0.0, "flag": False}, {"speed": 12.0, "flag": True}]
    got = checks.coverage(rows, ["speed", "flag"])
    assert got == {"speed": 1.0, "flag": 1.0}


def test_a_field_absent_from_every_row_scores_zero_rather_than_raising():
    """A missing column and an all-null column read identically downstream."""
    assert checks.coverage([{"a": 1}], ["b"]) == {"b": 0.0}


def test_coverage_of_no_rows_is_zero_not_a_division_error():
    assert checks.coverage([], ["a"]) == {"a": 0.0}


def test_check_coverage_fires_on_an_all_null_confidence_column():
    """The exact defect: envelope present, confidence entirely null."""
    rows = [{"vessel_id": "v", "ofac_ent_num": "1", "match_tier": "imo",
             "is_finding": True, "confidence": None, "ofac_name": "X",
             "sanctions_as_of": "2026-07-29"} for _ in range(50)]
    problems = checks.check_coverage("sanctioned_vessel_matches", rows)
    assert any("confidence" in p for p in problems), problems


def test_check_coverage_passes_a_fully_populated_table():
    rows = [{"vessel_id": "v", "ofac_ent_num": "1", "match_tier": "imo",
             "is_finding": True, "confidence": 0.95, "ofac_name": "X",
             "sanctions_as_of": "2026-07-29"} for _ in range(50)]
    assert checks.check_coverage("sanctioned_vessel_matches", rows) == []


def test_an_unfloored_field_is_reported_but_never_gates():
    """`None` floors are observations, not thresholds.

    This is the honesty rule applied to our own quality bars: a floor we have
    not measured on live data must not masquerade as one we have.
    """
    rows = [{"event_id": "e", "start_time": "t"} for _ in range(10)]
    assert checks.check_coverage("gfw_encounters", rows) == [], (
        "unmeasured fields must not fail the build"
    )
    report = dict((f, r) for f, r, _ in checks.coverage_report("gfw_encounters", rows))
    assert report["lat"] == 0.0, "but they must still be visible"
    assert report["event_id"] == 1.0


def test_check_coverage_fires_when_a_by_construction_field_goes_missing():
    rows = [{"event_id": None, "start_time": "t"} for _ in range(10)]
    problems = checks.check_coverage("gfw_encounters", rows)
    assert any("event_id" in p for p in problems), problems


def test_an_unknown_table_is_silent_rather_than_failing():
    """Tables without declared expectations must not block a landing."""
    assert checks.check_coverage("no_such_table", [{"a": 1}]) == []
    assert checks.coverage_report("no_such_table", [{"a": 1}]) == []


# ==========================================================================
# 3. the expectations table itself
# ==========================================================================

@pytest.mark.parametrize("table", checks.tables_with_expectations())
def test_every_floor_is_a_rate_or_an_explicit_none(table):
    for field, floor in checks.COVERAGE_EXPECTATIONS[table].items():
        assert floor is None or 0.0 <= floor <= 1.0, f"{table}.{field} = {floor}"


def test_no_floor_is_zero():
    """A 0.0 floor is a floor that cannot fail — say `None` and mean it.

    Writing 0.0 looks like a measured bar and enforces nothing; `None` says
    'observed, not gated', which is what such a field actually is.
    """
    zeros = [f"{t}.{f}"
             for t, exp in checks.COVERAGE_EXPECTATIONS.items()
             for f, v in exp.items() if v == 0.0]
    assert not zeros, f"use None instead of a 0.0 floor: {zeros}"


def test_the_envelope_fields_do_not_include_confidence():
    """CLAUDE.md §4.1 makes confidence nullable. A table that asserts it should
    floor it per-table, which is what caught the all-null column."""
    assert "confidence" not in checks.ENVELOPE_FIELDS
    assert set(checks.ENVELOPE_FIELDS) == {
        "source_id", "source_ref", "acquired_at", "ingested_at", "pipeline_version"}


def test_expectations_name_real_tables():
    """Guards against an expectation quietly applying to nothing.

    A typo'd table name would make `check_coverage` return [] forever — a
    silently-disabled check, which is worse than no check.
    """
    from maritime_isr.ingest import gfw_events, gfw_vessels, sanctions_match

    known = {s["table"] for s in gfw_events.EVENT_SPECS.values()}
    known |= {gfw_vessels.IDENTITY_TABLE, gfw_vessels.OWNERS_TABLE,
              gfw_vessels.CURRENT_TABLE, sanctions_match.MATCH_TABLE}
    unknown = set(checks.COVERAGE_EXPECTATIONS) - known
    assert not unknown, f"expectations declared for unknown tables: {unknown}"
