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
    assert "173 built" in out and "46 merged" in out, "the gap must be explained"


def test_report_landed_returns_what_it_printed():
    line = checks.report_landed("tag", "t", {"d": 1}, built=1)
    assert "landed 1" in line


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
