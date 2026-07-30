"""ADR-015 — one H3 helper, every resolution computed from coordinates.

These tests exist because the defect they prevent is invisible: two helpers at
different resolutions produce different cell ids, so joins between them return
nothing while every row count looks healthy.
"""
from __future__ import annotations

import pathlib

import h3
import pytest

from maritime_isr import h3util

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG = REPO / "maritime_isr"


# ==========================================================================
# there is exactly one helper
# ==========================================================================

def test_tiling_module_is_gone():
    """The duplicate helper must be deleted, not merely unused."""
    assert not (PKG / "tiling.py").exists(), (
        "tiling.py is back. Two H3 helpers is the ADR-015 defect."
    )


#: Modules allowed to touch the h3 library directly, with the reason.
#: `laptop_doctor` verifies that the h3 **v4 API name exists** — going through
#: h3util would defeat the check, since h3util installs a v3 fallback shim and
#: would therefore report success on a v3 install (CLAUDE.md §2 pins v4).
_H3_DIRECT_ALLOWED = {"h3util.py", "laptop_doctor.py"}


def test_no_module_computes_cells_outside_the_helper():
    """Only h3util may compute cells. One grid, one code path (ADR-003)."""
    offenders = []
    for p in PKG.rglob("*.py"):
        if p.name in _H3_DIRECT_ALLOWED:
            continue
        src = p.read_text(encoding="utf-8")
        if "latlng_to_cell" in src or "geo_to_h3" in src:
            offenders.append(p.relative_to(REPO))
    assert not offenders, (
        "these modules compute H3 cells themselves instead of using h3util: "
        f"{offenders}"
    )


def test_nothing_derives_a_coarse_cell_from_a_fine_one():
    """cell_to_parent disagrees with direct computation for ~7% of positions.

    Banned outright rather than used carefully — the resulting bug is
    position-dependent, intermittent and invisible in aggregate counts.
    """
    offenders = []
    for p in PKG.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        # the docstring in h3util explains the ban, so allow the word there
        if "cell_to_parent(" in src:
            offenders.append(p.relative_to(REPO))
    assert not offenders, f"parent-derivation found in {offenders} — see ADR-015"


def test_helper_offers_no_parent_function():
    assert not hasattr(h3util, "parent"), "the helper must not offer derivation"
    assert not hasattr(h3util, "cell_to_parent")


# ==========================================================================
# the measurement behind the ban
# ==========================================================================

def test_parent_and_direct_computation_actually_disagree():
    """Guards the *premise* of ADR-015, not just its conclusion.

    If a future h3 release made the hierarchy geometrically nested, this test
    fails and the ADR should be revisited rather than silently obeyed.
    """
    import random

    rng = random.Random(20260729)
    mismatch = 0
    n = 20_000
    for _ in range(n):
        lat = rng.uniform(5.0, 25.0)
        lon = rng.uniform(60.0, 78.0)
        if h3.cell_to_parent(h3util.cell(lat, lon, 7), 6) != h3util.cell(lat, lon, 6):
            mismatch += 1
    rate = mismatch / n
    assert rate > 0.01, (
        f"parent-vs-direct disagreement is only {rate:.3%}. If H3 changed, "
        "revisit ADR-015 rather than assuming the ban is still needed."
    )


# ==========================================================================
# resolutions
# ==========================================================================

def test_all_five_project_resolutions_are_declared():
    """4 coverage, 6 fusion gating, 7 join key, 8 static clustering, 9 fine."""
    assert h3util.RESOLUTIONS == (4, 6, 7, 8, 9)


def test_default_matches_the_old_tiling_default():
    """The unification must not silently re-tune the fusion core.

    tiling.cell defaulted to res 6; changing that would shift fusion behaviour
    and invalidate the baselines for a reason unrelated to the refactor.
    """
    from maritime_isr.config import H3_RESOLUTION

    assert h3util.DEFAULT_RES == 6 == H3_RESOLUTION


def test_index_all_returns_every_resolution():
    got = h3util.index_all(15.5, 68.2)
    assert set(got) == {f"h3_r{r}" for r in h3util.RESOLUTIONS}
    assert len(set(got.values())) == len(h3util.RESOLUTIONS), "cells must be distinct"


def test_each_resolution_is_computed_independently():
    """Every value must equal a direct computation at that resolution."""
    lat, lon = 8.720358, 65.061535  # a known parent/direct disagreement point
    got = h3util.index_all(lat, lon)
    for r in h3util.RESOLUTIONS:
        assert got[f"h3_r{r}"] == h3.latlng_to_cell(lat, lon, r)


def test_index_both_still_returns_the_adr003_pair():
    r7, r9 = h3util.index_both(15.5, 68.2)
    assert r7 == h3util.cell(15.5, 68.2, 7)
    assert r9 == h3util.cell(15.5, 68.2, 9)


def test_neighbors_is_the_same_function_as_disk():
    """Two names, one implementation — not two implementations."""
    assert h3util.neighbors is h3util.disk


# ==========================================================================
# the join that was broken
# ==========================================================================

def test_ingest_rows_carry_the_resolution_fusion_joins_on():
    """The actual ADR-015 failure: ingest stamped 7/9, fusion joins on 6.

    An existence check only. The real guard is the query test below — a column
    being present says nothing about whether its values join.
    """
    from maritime_isr.ingest.landing import stamp_h3

    row = {"lat": 15.5, "lon": 68.2}
    stamp_h3(row)
    assert "h3_r6" in row, "without res 6, ingest tables cannot join fusion tables"
    assert row["h3_r6"] == h3util.cell(15.5, 68.2, h3util.DEFAULT_RES)


# --- the guard with teeth: run the join, in DuckDB, on real output ---------

#: Positions from landed GFW encounter/gap events in the AOI. Real coordinates
#: rather than round numbers, because cell boundaries fall where they fall and
#: a tidy 15.0/68.0 could sit comfortably inside one cell by luck.
_AOI_POSITIONS = [
    (18.9732, 71.6237),
    (15.4411, 68.2098),
    (12.0873, 74.8331),
    (21.5502, 69.0417),
    (8.7204, 65.0615),
]


def _land_ingest_side(tmp_path, monkeypatch) -> int:
    """Land an ingest table exactly as a connector does. Returns rows landed."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope, stamp_h3

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    import datetime as dt

    rows = []
    for i, (lat, lon) in enumerate(_AOI_POSITIONS):
        r = {"event_id": f"enc-{i}", "lat": lat, "lon": lon,
             "start_time": dt.datetime(2026, 6, 14, tzinfo=dt.timezone.utc)}
        stamp_h3(r)
        stamp_envelope(r, source_id="gfw-events", source_ref=f"enc:{i}",
                       acquired_at=r["start_time"])
        rows.append(r)
    written = land_table(rows, table="gfw_encounters",
                         key_fields=("event_id",), day_field="start_time")
    return sum(written.values())


def _run_fusion_side():
    """Run the real fusion cascade and return its verdict rows.

    This calls `fusion.dark.dark_cascade`, not a hand-built dict — the whole
    point is to compare against the cell the fusion core *actually* computes.
    Empty track and static lists are fine: the cascade still emits one verdict
    row per contact, and the verdict is not what is under test here.
    """
    import datetime as dt

    import pandas as pd

    from maritime_isr.fusion.dark import dark_cascade
    from maritime_isr.tracks.coverage import CoverageModel

    ts = pd.Timestamp(dt.datetime(2026, 6, 14, 6, 0, tzinfo=dt.timezone.utc))
    t0 = ts.timestamp() - 3600

    # Coverage evidence at the same places and times, so the contacts are not
    # all suppressed for deafness — closer to how the cascade really runs.
    ais = pd.DataFrame([
        {"ts": ts, "lat": lat, "lon": lon, "receiver": "ter:r1|sat:s1",
         "mmsi": 400000000 + i}
        for i, (lat, lon) in enumerate(_AOI_POSITIONS)
    ])
    model = CoverageModel(t0).fit(ais)

    unmatched = [
        {"detection_id": f"det-{i}", "scene_id": "S1_TEST_0001", "ts": ts,
         "lat": lat, "lon": lon, "length_m": 180.0, "score": 0.9}
        for i, (lat, lon) in enumerate(_AOI_POSITIONS)
    ]
    return dark_cascade(unmatched, model, statics=[], tracks=[])


def test_a_landed_ingest_table_actually_joins_a_fusion_table(tmp_path, monkeypatch):
    """Run the join. Assert rows come back.

    ADR-015 was a *join* failure, and a join failure is silent: both sides have
    healthy row counts, both have an h3 column, and the query returns nothing.
    Asserting that ingest stamps res 6 is an existence check and would not have
    caught it if fusion had moved instead. So this executes the query.
    """
    import duckdb

    landed = _land_ingest_side(tmp_path, monkeypatch)
    assert landed == len(_AOI_POSITIONS), "ingest side did not land"

    verdicts = _run_fusion_side()
    assert verdicts, "fusion side produced nothing to join against"

    from maritime_isr.ingest.landing import table_glob

    con = duckdb.connect()
    try:
        fusion_rows = [{"candidate_id": v["candidate_id"], "h3_cell": v["h3_cell"]}
                       for v in verdicts]
        con.execute("CREATE TABLE dark_candidates (candidate_id VARCHAR, h3_cell VARCHAR)")
        con.executemany("INSERT INTO dark_candidates VALUES (?, ?)",
                        [(r["candidate_id"], r["h3_cell"]) for r in fusion_rows])

        n = con.execute(
            f"""
            SELECT count(*)
            FROM read_parquet('{table_glob("gfw_encounters")}') i
            JOIN dark_candidates d ON i.h3_r6 = d.h3_cell
            """
        ).fetchone()[0]
        assert n > 0, (
            "the ingest table and the fusion table share no H3 cell. This is "
            "the ADR-015 defect: both sides look healthy, the join returns "
            "nothing, and no row count anywhere reveals it."
        )
        assert n == len(_AOI_POSITIONS), (
            f"expected one join hit per position, got {n}"
        )

        # Negative control: the same query at the wrong resolution must find
        # nothing. Without this, a test that joined on a column of NULLs
        # against a column of NULLs could pass and prove nothing.
        wrong = con.execute(
            f"""
            SELECT count(*)
            FROM read_parquet('{table_glob("gfw_encounters")}') i
            JOIN dark_candidates d ON i.h3_r7 = d.h3_cell
            """
        ).fetchone()[0]
        assert wrong == 0, (
            "res 7 joined a res 6 fusion cell — the resolutions are no longer "
            "distinguishable and this guard has stopped guarding anything"
        )
    finally:
        con.close()


def test_fusion_cell_equals_the_ingest_stamp_position_by_position(tmp_path, monkeypatch):
    """Same coordinate through both code paths must give the same cell.

    The join test proves *some* rows match. This proves every one does, which
    is what makes a missing match a real signal rather than sampling luck.
    """
    from maritime_isr.ingest.landing import stamp_h3

    verdicts = _run_fusion_side()
    assert len(verdicts) == len(_AOI_POSITIONS)
    for (lat, lon), v in zip(_AOI_POSITIONS, verdicts):
        row = {"lat": lat, "lon": lon}
        stamp_h3(row)
        assert row["h3_r6"] == v["h3_cell"], (
            f"ingest and fusion disagree at {lat},{lon}"
        )


def test_stamped_cells_match_what_the_fusion_core_would_compute():
    """End to end: a landed ingest row and a fusion-side cell must agree."""
    from maritime_isr.ingest.landing import stamp_h3

    lat, lon = 18.9732, 71.6237  # from a real landed loitering event
    row = {"lat": lat, "lon": lon}
    stamp_h3(row)
    assert row["h3_r6"] == h3util.cell(lat, lon)      # fusion default
    assert row["h3_r8"] == h3util.cell(lat, lon, 8)   # static clustering
    assert row["h3_r4"] == h3util.cell(lat, lon, 4)   # coverage model


def test_stamp_h3_leaves_positionless_rows_alone():
    from maritime_isr.ingest.landing import stamp_h3

    row = {"event_id": "no-position"}
    stamp_h3(row)
    assert not any(k.startswith("h3_r") for k in row)


def test_footprint_cells_survived_the_migration():
    """It only existed in tiling.py; losing it would break scene joins."""
    poly = [(5.0, 60.0), (5.0, 61.0), (6.0, 61.0), (6.0, 60.0)]
    cells = h3util.footprint_cells(poly, 5)
    assert cells and all(isinstance(c, str) for c in cells)
